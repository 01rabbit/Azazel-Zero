#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PY_ROOT = REPO_ROOT / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from azazel_zero.first_minute.config import FirstMinuteConfig
from azazel_zero.first_minute.controller import FirstMinuteController
from azazel_zero.first_minute.nft import NftManager
from azazel_zero.first_minute.probes import run_all
from azazel_zero.first_minute.state_machine import Stage
from azazel_zero.first_minute.tc import TcManager


def parse_args() -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="configs/first_minute.yaml", help="設定ファイルパス (YAML)")
    common.add_argument("--dry-run", action="store_true", help="nft/tcを適用せず計画だけ表示")
    common.add_argument("--no-dns-start", action="store_true", help="dnsmasqを起動しない (外部dnsmasq使用時)")
    common.add_argument("--foreground", action="store_true", help="フォアグラウンド実行（デフォルト）")
    common.add_argument("--daemonize", action="store_true", help="バックグラウンド実行")
    common.add_argument("--pretty-console", action="store_true", help="コンソールに人間向けサマリを表示（ログはJSONで継続）")

    parser = argparse.ArgumentParser(
        parents=[common],
        description="Azazel-Zero First-Minute Control runner (systemd不要のデバッグ・開発用)。ステージ切替・プローブ・クリーンアップをCLIで操作できます。",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", parents=[common], add_help=False, help="コントローラ起動")
    sub.add_parser("stop", parents=[common], add_help=False, help="デーモンにSIGTERMを送って停止")
    sub.add_parser("status", parents=[common], add_help=False, help="PID表示とローカルAPIの簡易ステータス取得")
    sub.add_parser("probe-now", parents=[common], add_help=False, help="安全プローブのみ即時実行して結果表示")
    force = sub.add_parser("force-state", parents=[common], add_help=False, help="指定ステージへ強制遷移 (tc/nft適用)")
    force.add_argument("state", choices=[s.value for s in Stage], help="目標ステージ")
    sub.add_parser("dry-run", parents=[common], add_help=False, help="nft/tcテンプレートを表示 (変更なし)")
    cleanup = sub.add_parser("cleanup", parents=[common], add_help=False, help="本プログラムが設定したnft/tc/dnsmasq(任意)を初期化")
    cleanup.add_argument("--kill-dnsmasq", action="store_true", help="dnsmasq-first_minute.confで起動したdnsmasqをpkillで落とす")
    sub.add_parser("help", parents=[common], add_help=False, help="このヘルプを表示")

    args = parser.parse_args()
    if args.command in (None, "help"):
        parser.print_help()
        sys.exit(0)
    return args


def write_pid(pid_file: Path) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


def read_pid(pid_file: Path) -> int:
    if not pid_file.exists():
        raise SystemExit("No PID file found; is the daemon running?")
    return int(pid_file.read_text().strip())


def setup_logging(cfg: FirstMinuteConfig) -> None:
    cfg.ensure_dirs()
    log_path = cfg.log_dir / "first_minute.log"
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(log_path))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(message)s")


def cmd_start(args: argparse.Namespace, cfg: FirstMinuteConfig) -> None:
    if args.daemonize and args.foreground:
        raise SystemExit("Choose either --foreground or --daemonize, not both.")
    if os.geteuid() != 0:
        print("start: root 権限が必要です (sudo を使用してください)")
        sys.exit(1)
    ctrl = FirstMinuteController(cfg, dry_run=args.dry_run, no_dns_start=args.no_dns_start, pretty_console=args.pretty_console)
    if args.daemonize:
        pid = os.fork()
        if pid > 0:
            print(f"Daemon started with PID {pid}")
            return
        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            os._exit(0)
        write_pid(cfg.pid_file)
        ctrl.start()
    else:
        write_pid(cfg.pid_file)
        ctrl.start()


def cmd_stop(cfg: FirstMinuteConfig) -> None:
    pid = read_pid(cfg.pid_file)
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to {pid}")
    except PermissionError:
        print("stop: プロセスにSIGTERMを送れませんでした（権限不足か既に終了）")


def cmd_status(cfg: FirstMinuteConfig) -> None:
    try:
        pid = read_pid(cfg.pid_file)
        print(f"Daemon PID: {pid}")
    except SystemExit:
        print("Daemon not running.")
    url = f"http://{cfg.status_api.get('host', '127.0.0.1')}:{cfg.status_api.get('port', 8081)}/"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            print(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"Status API unavailable: {exc}")


def cmd_probe_now(cfg: FirstMinuteConfig) -> None:
    out = run_all(cfg.probes, cfg.interfaces["upstream"])
    print(json.dumps(out.details, indent=2))


def cmd_force_state(cfg: FirstMinuteConfig, state: str) -> None:
    stage = Stage(state)
    if os.geteuid() != 0:
        print("force-state: root 権限が必要です (sudo を使用してください)")
        sys.exit(1)
    nft = NftManager(
        cfg.nft_template_path,
        cfg.interfaces["upstream"],
        cfg.interfaces["downstream"],
        cfg.interfaces["mgmt_ip"],
        cfg.interfaces["mgmt_subnet"],
        int(cfg.policy.get("probe_allow_ttl", 120)),
        int(cfg.policy.get("dynamic_allow_ttl", 300)),
    )
    tc = TcManager(cfg.interfaces["downstream"], cfg.interfaces["upstream"])
    try:
        nft.set_stage(stage)
    except Exception:
        nft.apply_base()
        nft.set_stage(stage)
    tc.apply(stage)
    print(f"Forced stage -> {stage.value}")


def cmd_dry_run(cfg: FirstMinuteConfig) -> None:
    nft = NftManager(
        cfg.nft_template_path,
        cfg.interfaces["upstream"],
        cfg.interfaces["downstream"],
        cfg.interfaces["mgmt_ip"],
        cfg.interfaces["mgmt_subnet"],
        int(cfg.policy.get("probe_allow_ttl", 120)),
        int(cfg.policy.get("dynamic_allow_ttl", 300)),
    )
    print("=== nftables preview ===")
    print(nft.render_preview())
    print("=== tc stages ===")
    print("PROBE: netem 220ms/100ms; tbf 1mbit")
    print("DEGRADED: netem 150ms/50ms; tbf 2mbit")
    print("CONTAIN: netem 400ms/200ms loss 5%; tbf 512kbit")


def cmd_cleanup(cfg: FirstMinuteConfig, kill_dnsmasq: bool) -> None:
    """Flush nft/tc artifacts and optionally stop dnsmasq started for First-Minute."""
    if os.geteuid() != 0:
        print("cleanup: root 権限が必要です (sudo を使用してください)")
        sys.exit(1)
    nft = NftManager(
        cfg.nft_template_path,
        cfg.interfaces["upstream"],
        cfg.interfaces["downstream"],
        cfg.interfaces["mgmt_ip"],
        cfg.interfaces["mgmt_subnet"],
        int(cfg.policy.get("probe_allow_ttl", 120)),
        int(cfg.policy.get("dynamic_allow_ttl", 300)),
    )
    tc = TcManager(cfg.interfaces["downstream"], cfg.interfaces["upstream"])
    nft.clear()
    tc.clear()
    if kill_dnsmasq:
        subprocess.run(
            ["pkill", "-f", f"dnsmasq.*{cfg.dnsmasq_conf_path.name}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    try:
        cfg.pid_file.unlink()
    except FileNotFoundError:
        pass
    print("Cleanup complete (nft/tc flushed{})".format(", dnsmasq stopped" if kill_dnsmasq else ""))


def main() -> None:
    args = parse_args()
    cfg = FirstMinuteConfig.load(args.config)
    setup_logging(cfg)
    if args.command == "start":
        cmd_start(args, cfg)
    elif args.command == "stop":
        cmd_stop(cfg)
    elif args.command == "status":
        cmd_status(cfg)
    elif args.command == "probe-now":
        cmd_probe_now(cfg)
    elif args.command == "force-state":
        cmd_force_state(cfg, args.state)
    elif args.command == "dry-run":
        cmd_dry_run(cfg)
    elif args.command == "cleanup":
        cmd_cleanup(cfg, args.kill_dnsmasq)
    else:
        raise SystemExit("Unknown command")


if __name__ == "__main__":
    main()
