"""CLI for quick ops without the server.

  python invest_cli.py summary
  python invest_cli.py signals
  python invest_cli.py import <account-id> <csv-in-imports/>
  python invest_cli.py wint <account-id> <holding-or-master.xlsx> [--cashflow <upcoming-cashflow.xlsx>] [--summary <master.xlsx>]
  python invest_cli.py ipo add "Name" 2026-07-10 --total 12.5 --qib 20 --nii 15 --retail 3 --gmp 45
  python invest_cli.py ipo list [--closed]
  python invest_cli.py ipo remind
  python invest_cli.py ipo sync-upstox              (populate tracker from Upstox IPO API — adds symbols)
  python invest_cli.py ipo applied "Name" --symbol XYZ --allotment-date 2026-07-15 --listing-date 2026-07-18
  python invest_cli.py ipo allotments               (applied IPOs: allotted?/awaiting + listing-day P&L)
  python invest_cli.py ipo-history refresh          (NSE list + Chittorgarh prices/subscription + live tracker)
  python invest_cli.py ipo-history import <csv-in-imports/>
  python invest_cli.py ipo-history list
"""

import argparse
import json

import config
from investlib import analysis, ipo, ipo_history, portfolio


def _print(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(prog="investments")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary")
    sub.add_parser("signals")

    p_import = sub.add_parser("import")
    p_import.add_argument("account")
    p_import.add_argument("file", help="CSV filename inside imports/")

    p_wint = sub.add_parser("wint")
    p_wint.add_argument("account")
    p_wint.add_argument("holding_file", help="Wint Wealth 'Holding Statement' (or the master) xlsx, filename inside imports/")
    p_wint.add_argument("--cashflow", help="Wint Wealth 'Upcoming Cashflow Statement' xlsx, filename inside imports/")
    p_wint.add_argument("--summary", help="Wint Wealth master/'Investment Summary' xlsx for purchase dates, filename inside imports/")

    p_ipo = sub.add_parser("ipo")
    ipo_sub = p_ipo.add_subparsers(dest="ipo_cmd", required=True)
    p_add = ipo_sub.add_parser("add")
    p_add.add_argument("name")
    p_add.add_argument("close_date", help="YYYY-MM-DD")
    p_add.add_argument("--total", type=float, default=0.0)
    p_add.add_argument("--qib", type=float, default=0.0)
    p_add.add_argument("--nii", type=float, default=0.0)
    p_add.add_argument("--retail", type=float, default=0.0)
    p_add.add_argument("--gmp", type=float, default=None)
    p_add.add_argument("--notes", default="")
    p_list = ipo_sub.add_parser("list")
    p_list.add_argument("--closed", action="store_true")
    ipo_sub.add_parser("remind")
    ipo_sub.add_parser("sync-upstox")  # populate tracker from Upstox IPO API (adds symbols)
    p_applied = ipo_sub.add_parser("applied")
    p_applied.add_argument("name")
    p_applied.add_argument("--symbol", default=None, help="exchange symbol (for allotment matching)")
    p_applied.add_argument("--lots", type=float, default=None)
    p_applied.add_argument("--amount", type=float, default=None)
    p_applied.add_argument("--allotment-date", default=None, help="YYYY-MM-DD")
    p_applied.add_argument("--listing-date", default=None, help="YYYY-MM-DD")
    p_applied.add_argument("--undo", action="store_true", help="unmark as applied")
    ipo_sub.add_parser("allotments")

    p_hist = sub.add_parser("ipo-history")
    hist_sub = p_hist.add_subparsers(dest="hist_cmd", required=True)
    hist_sub.add_parser("refresh")
    p_hist_import = hist_sub.add_parser("import")
    p_hist_import.add_argument("file", help="CSV filename inside imports/")
    hist_sub.add_parser("list")

    args = parser.parse_args()
    if args.cmd == "summary":
        _print(analysis.summary())
    elif args.cmd == "signals":
        _print(analysis.signals())
    elif args.cmd == "import":
        result = portfolio.import_holdings(args.account, config.IMPORTS_DIR / args.file)
        print(f"imported {len(result['rows'])} positions into {args.account} (as of {result['as_of']})")
    elif args.cmd == "wint":
        cashflow_path = config.IMPORTS_DIR / args.cashflow if args.cashflow else None
        summary_path = config.IMPORTS_DIR / args.summary if args.summary else None
        result = portfolio.import_wint_statement(
            args.account, config.IMPORTS_DIR / args.holding_file, cashflow_path, summary_path)
        print(f"imported {len(result['rows'])} bonds into {args.account} (as of {result['as_of']})")
    elif args.cmd == "ipo":
        if args.ipo_cmd == "add":
            record = ipo.upsert(args.name, args.close_date, sub_total=args.total,
                                sub_qib=args.qib, sub_nii=args.nii, sub_retail=args.retail,
                                gmp=args.gmp, notes=args.notes)
            _print({**record, **ipo.recommend(record)})
        elif args.ipo_cmd == "list":
            _print(ipo.tracked(include_closed=args.closed))
        elif args.ipo_cmd == "remind":
            items = ipo.reminders()
            if not items:
                print("no IPOs closing in the next 2 days")
            for item in items:
                print(f"[{item['call']}] {item['name']} closes {item['close_date']} — {item['reason']}")
        elif args.ipo_cmd == "applied":
            record = ipo.set_applied(
                args.name, applied=not args.undo, symbol=args.symbol,
                applied_lots=args.lots, applied_amount=args.amount,
                allotment_date=args.allotment_date, listing_date=args.listing_date)
            _print(record)
        elif args.ipo_cmd == "sync-upstox":
            from investlib import ipo_fetch
            result = ipo_fetch.refresh_from_upstox()
            print(f"Upstox: updated {result['count']} IPO(s) - {', '.join(result['updated']) or 'none'}")
        elif args.ipo_cmd == "allotments":
            _print(ipo.allotment_status())
    elif args.cmd == "ipo-history":
        if args.hist_cmd == "refresh":
            nse = ipo_history.fetch_from_nse()
            chittorgarh = ipo_history.fetch_from_chittorgarh()
            tracker = ipo_history.carry_from_tracker()
            print(f"NSE: +{nse['added']} new / {nse['updated']} updated; "
                  f"Chittorgarh: +{chittorgarh['added']} new / {chittorgarh['updated']} updated; "
                  f"tracker: +{tracker['added']} new / {tracker['updated']} updated; "
                  f"{len(ipo_history.records())} IPOs in history")
        elif args.hist_cmd == "import":
            result = ipo_history.import_csv(config.IMPORTS_DIR / args.file)
            print(f"imported {result['count']} rows "
                  f"(+{result['added']} new / {result['updated']} updated)")
        elif args.hist_cmd == "list":
            _print(ipo_history.records())


if __name__ == "__main__":
    main()
