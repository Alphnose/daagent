"""Direct Cloud Bigtable client tool (emergency fallback for the MCP toolset).

The agent's primary path to Bigtable is the declarative MCP contract served by the
``mcp-toolbox-bigtable`` Cloud Run microservice (see
:mod:`app.tools.bigtable_mcp_tool`). This module implements the same read as a
native client call so operations staff keep real-time visibility when the
microservice is unreachable (offline CI, VPC-SC sandbox, cold-start outage).

All identifiers are resolved from :mod:`app.config`; infrastructure exceptions are
logged internally and masked in the user-facing response.
"""

import logging
import struct
from typing import Optional, Union

from google.cloud import bigtable
from google.cloud.bigtable.row_set import RowSet

from app.config import (
    get_bigtable_instance_id,
    get_bigtable_table_id,
    get_project_id,
)

logger = logging.getLogger(__name__)

# Columns written by the streaming pipeline as big-endian encoded numbers.
_FLOAT_COLUMNS = {
    "cashier_1h_avg_discount_pct",
    "cashier_1h_promo_rate",
    "cashier_1h_total_discount_usd",
    "risk_score",
}
_INT_COLUMNS = {
    "cashier_1h_txn_count",
    "cashier_1h_manual_override_count",
    "cashier_1h_promo_count",
}

_bt_client = None
_bt_table = None


def get_table():
    """Lazily resolves the Bigtable table handle for the active environment."""
    global _bt_client, _bt_table
    if _bt_table is None:
        _bt_client = bigtable.Client(project=get_project_id())
        instance = _bt_client.instance(get_bigtable_instance_id())
        _bt_table = instance.table(get_bigtable_table_id())
    return _bt_table


def _decode_float(raw: bytes) -> Union[float, str]:
    """Decodes a big-endian float64 cell (mirrors Bigtable SQL ``TO_FLOAT64``)."""
    if len(raw) != 8:
        return raw.decode("utf-8", errors="replace")
    return struct.unpack(">d", raw)[0]


def _decode_int(raw: bytes) -> Union[int, str]:
    if len(raw) != 8:
        return raw.decode("utf-8", errors="replace")
    return struct.unpack(">q", raw)[0]


def normalize_row_key_prefix(store_id: str, cashier_id: str) -> str:
    """Normalizes loose identifiers into the canonical ``STORE_048#CASH_1190`` prefix."""
    if not store_id.startswith("STORE_"):
        store_id = f"STORE_{store_id.zfill(3)}"
    if not cashier_id.startswith("CASH_"):
        cashier_id = f"CASH_{cashier_id}"
    return f"{store_id}#{cashier_id}"


def read_cashier_realtime_metrics(store_id: str, cashier_id: str) -> str:
    """Reads live 1-hour rolling metrics, anomaly risk score, and audit status flags for a cashier from Cloud Bigtable.

    Args:
        store_id: The store identifier, e.g. 'STORE_048' or '048'.
        cashier_id: The cashier identifier, e.g. 'CASH_1190' or '1190'.

    Returns:
        Formatted metrics including 1-hour transaction count, average discount %, total discount USD, promo rate, risk score, and audit status.
    """
    prefix = normalize_row_key_prefix(store_id, cashier_id)
    store_id, cashier_id = prefix.split("#", 1)

    try:
        table = get_table()
        row_set = RowSet()
        row_set.add_row_range_with_prefix(prefix)
        # Row keys embed a reverse timestamp, so the first match is the newest record.
        rows = list(table.read_rows(row_set=row_set, limit=1))
    except Exception as exc:
        logger.error("Bigtable query failed for prefix %s: %s", prefix, exc)
        return (
            "The real-time cashier metrics store is currently unreachable due to a "
            "temporary service failure. Please try again later."
        )

    if not rows:
        return f"No realtime records found in Bigtable for cashier '{cashier_id}' at store '{store_id}'."

    row = rows[0]
    metrics = {
        "store_id": store_id,
        "cashier_id": cashier_id,
        "row_key": row.row_key.decode("utf-8"),
    }

    for _family, columns in row.cells.items():
        for col_name, cell_list in columns.items():
            name = col_name.decode("utf-8")
            raw = cell_list[0].value
            if name in _FLOAT_COLUMNS:
                metrics[name] = _decode_float(raw)
            elif name in _INT_COLUMNS:
                metrics[name] = _decode_int(raw)
            else:
                metrics[name] = raw.decode("utf-8", errors="replace")

    def _num(key: str, default: float = 0.0) -> float:
        value = metrics.get(key, default)
        return value if isinstance(value, (int, float)) else default

    output = [
        f"### Bigtable Live Cashier Metrics: {store_id} / {cashier_id}",
        f"- **Audit Status**: {metrics.get('audit_status', 'UNKNOWN')}",
        f"- **Anomaly Risk Score**: {_num('risk_score'):.6f}",
        f"- **1-Hour Transaction Count**: {metrics.get('cashier_1h_txn_count', 0)}",
        f"- **1-Hour Average Discount**: {_num('cashier_1h_avg_discount_pct'):.2f}%",
        f"- **1-Hour Total Discount (USD)**: ${_num('cashier_1h_total_discount_usd'):.2f}",
        f"- **1-Hour Promotion Rate**: {_num('cashier_1h_promo_rate') * 100:.2f}%",
        f"- **1-Hour Promo Count**: {metrics.get('cashier_1h_promo_count', 0)}",
        f"- **1-Hour Manual Override Count**: {metrics.get('cashier_1h_manual_override_count', 0)}",
        f"- **Last Event Timestamp**: {metrics.get('last_event_ts', 'N/A')}",
        f"- **Bigtable Row Key**: `{metrics.get('row_key')}`",
    ]

    return "\n".join(output)
