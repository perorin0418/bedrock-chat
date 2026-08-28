"""Generate a dummy CSV matching the shape of
GET /admin/claude-teams-tokens/usage-history/csv, for testing external
graphing tools without needing real production data.

Usage: python scripts/gen_dummy_usage_csv.py OUTPUT.csv
       (writing via stdout redirection on Windows double-translates the
       CSV module's \\r\\n line endings into \\r\\r\\n, so this takes an
       explicit output path and opens it with newline="" instead)
"""

import csv
import random
import sys
from datetime import datetime, timedelta, timezone

random.seed(42)

TOKEN_COUNT = 20
TOKENS = [
    (f"tok-{i:04x}{i:04x}", f"prod-token-{i + 1}") for i in range(TOKEN_COUNT)
]

START = datetime(2026, 5, 10, 0, 0, 0, tzinfo=timezone.utc)
HOURS = 24 * 90  # ~3 months of hourly samples


def generate_rows():
    rows = []
    for token_id, display_name in TOKENS:
        five_hour_bucket_start = START
        seven_day_bucket_start = START
        five_hour_util = 0.0
        seven_day_util = 0.0

        # Simulate one auth_error incident (token briefly expired/revoked)
        # so the sample data exercises the "gap" case too.
        auth_error_start = random.randint(HOURS // 3, HOURS // 2)
        auth_error_len = random.randint(2, 5)

        for h in range(HOURS):
            t = START + timedelta(hours=h)
            sampled_at_ms = int(t.timestamp() * 1000)

            if (t - five_hour_bucket_start).total_seconds() >= 5 * 3600:
                five_hour_bucket_start = t
                five_hour_util = 0.0
            five_hour_resets_at = (
                five_hour_bucket_start + timedelta(hours=5)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            if (t - seven_day_bucket_start).total_seconds() >= 7 * 24 * 3600:
                seven_day_bucket_start = t
                seven_day_util = 0.0
            seven_day_resets_at = (
                seven_day_bucket_start + timedelta(days=7)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            if auth_error_start <= h < auth_error_start + auth_error_len:
                fetch_status = "auth_error"
                fetch_error_message = "401 Unauthorized: token expired or revoked"
                five_hour_val = ""
                seven_day_val = ""
                fh_reset = ""
                sd_reset = ""
            else:
                fetch_status = "ok"
                fetch_error_message = ""
                five_hour_util = min(100.0, five_hour_util + random.uniform(3, 14))
                seven_day_util = min(100.0, seven_day_util + random.uniform(0.3, 1.8))
                five_hour_val = round(five_hour_util, 1)
                seven_day_val = round(seven_day_util, 1)
                fh_reset = five_hour_resets_at
                sd_reset = seven_day_resets_at

            rows.append(
                [
                    token_id,
                    display_name,
                    sampled_at_ms,
                    fetch_status,
                    fetch_error_message,
                    five_hour_val,
                    fh_reset,
                    seven_day_val,
                    sd_reset,
                ]
            )

    rows.sort(key=lambda r: (r[0], r[2]))
    return rows


def main():
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} OUTPUT.csv", file=sys.stderr)
        sys.exit(1)
    out_path = sys.argv[1]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "token_id",
                "display_name",
                "sampled_at_ms",
                "fetch_status",
                "fetch_error_message",
                "five_hour_utilization",
                "five_hour_resets_at",
                "seven_day_utilization",
                "seven_day_resets_at",
            ]
        )
        writer.writerows(generate_rows())


if __name__ == "__main__":
    main()
