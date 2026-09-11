"""Bigtable tool querying live operational cashier metrics and audit statuses."""

import os
import struct
from typing import Dict, Any, Optional
from google.cloud import bigtable
from google.cloud.bigtable.row_set import RowSet

import logging

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "antigravity-503007")
BIGTABLE_INSTANCE_ID = os.environ.get("BIGTABLE_INSTANCE_ID", "operations-db")
BIGTABLE_TABLE_ID = os.environ.get("BIGTABLE_TABLE_ID", "cashier_realtime_alerts")

_bt_client = None
_bt_table = None

def get_table():
    global _bt_client, _bt_table
    if _bt_table is None:
        _bt_client = bigtable.Client(project=PROJECT_ID)
        instance = _bt_client.instance(BIGTABLE_INSTANCE_ID)
        _bt_table = instance.table(BIGTABLE_TABLE_ID)
    return _bt_table

def read_cashier_realtime_metrics(store_id: str, cashier_id: str) -> str:
    """Reads live 1-hour rolling metrics, anomaly risk score, and audit status flags for a cashier from Cloud Bigtable.
    
    Args:
        store_id: The store identifier, e.g. 'STORE_048' or '048'.
        cashier_id: The cashier identifier, e.g. 'CASH_1190' or '1190'.
        
    Returns:
        Formatted metrics including 1-hour transaction count, average discount %, total discount USD, promo rate, risk score, and audit status.
    """
    if not store_id.startswith("STORE_"):
        store_id = f"STORE_{store_id.zfill(3)}"
    if not cashier_id.startswith("CASH_"):
        cashier_id = f"CASH_{cashier_id}"
        
    prefix = f"{store_id}#{cashier_id}"
    try:
        table = get_table()
        row_set = RowSet()
        row_set.add_row_range_with_prefix(prefix)
        # Read the latest alert record (Bigtable row keys have reverse timestamps, so first match is newest)
        rows = list(table.read_rows(row_set=row_set, limit=1))
    except Exception as e:
        logger.error("Bigtable query failed for prefix %s: %s", prefix, e)
        return "The requested Bigtable store data is currently unreachable due to a temporary service failure. Please try again later."
    if not rows:
        return f"No realtime records found in Bigtable for cashier '{cashier_id}' at store '{store_id}'."
        
    row = rows[0]
    metrics = {
        "store_id": store_id,
        "cashier_id": cashier_id,
        "row_key": row.row_key.decode("utf-8")
    }
    
    for cf, cols in row.cells.items():
        for col_name, cell_list in cols.items():
            name = col_name.decode("utf-8")
            val_bytes = cell_list[0].value
            
            # Decode based on column type
            if name in ["cashier_1h_avg_discount_pct", "cashier_1h_promo_rate", "cashier_1h_total_discount_usd", "risk_score"]:
                if len(val_bytes) == 8:
                    metrics[name] = struct.unpack(">d", val_bytes)[0]
                else:
                    metrics[name] = val_bytes.decode("utf-8", errors="replace")
            elif name in ["cashier_1h_txn_count", "cashier_1h_manual_override_count", "cashier_1h_promo_count"]:
                if len(val_bytes) == 8:
                    metrics[name] = struct.unpack(">q", val_bytes)[0]
                else:
                    metrics[name] = val_bytes.decode("utf-8", errors="replace")
            else:
                metrics[name] = val_bytes.decode("utf-8", errors="replace")
                
    output = [
        f"### Bigtable Live Cashier Metrics: {store_id} / {cashier_id}",
        f"- **Audit Status**: {metrics.get('audit_status', 'UNKNOWN')}",
        f"- **Anomaly Risk Score**: {metrics.get('risk_score', 0.0):.6f}",
        f"- **1-Hour Transaction Count**: {metrics.get('cashier_1h_txn_count', 0)}",
        f"- **1-Hour Average Discount**: {metrics.get('cashier_1h_avg_discount_pct', 0.0)*100:.2f}%",
        f"- **1-Hour Total Discount (USD)**: ${metrics.get('cashier_1h_total_discount_usd', 0.0):.2f}",
        f"- **1-Hour Promotion Rate**: {metrics.get('cashier_1h_promo_rate', 0.0)*100:.2f}%",
        f"- **1-Hour Promo Count**: {metrics.get('cashier_1h_promo_count', 0)}",
        f"- **1-Hour Manual Override Count**: {metrics.get('cashier_1h_manual_override_count', 0)}",
        f"- **Last Event Timestamp**: {metrics.get('last_event_ts', 'N/A')}",
        f"- **Bigtable Row Key**: `{metrics.get('row_key')}`"
    ]
    
    return "\n".join(output)
