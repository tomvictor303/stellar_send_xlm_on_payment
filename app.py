import configparser
import os
import sys
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from stellar_sdk import Server, Keypair, TransactionBuilder, Network, Asset

# =========================
# Configuration
# =========================
config = configparser.ConfigParser()
config.read('config.txt')

DISTRIBUTOR_SECRET_KEY = config['DEFAULT'].get('DISTRIBUTOR_SECRET_KEY', '')
RECEIVER_ADDRESS = config['DEFAULT'].get('RECEIVER_ADDRESS', '')

if not DISTRIBUTOR_SECRET_KEY:
    print("ERROR: DISTRIBUTOR_SECRET_KEY not set", file=sys.stderr)
    sys.exit(1)

if not RECEIVER_ADDRESS:
    print("ERROR: RECEIVER_ADDRESS not set", file=sys.stderr)
    sys.exit(1)

# =========================
# Constants
# =========================
HORIZON_URL = "https://horizon.stellar.org"
SEND_PERCENT = Decimal("0.25")
MIN_INCOMING_XLM = Decimal("0")

CURSOR_FILE = "cursor.txt"

# =========================
# Stellar setup
# =========================
server = Server(HORIZON_URL)
DISTRIBUTOR_KP = Keypair.from_secret(DISTRIBUTOR_SECRET_KEY)
DISTRIBUTOR_ADDRESS = DISTRIBUTOR_KP.public_key

os.makedirs("logs", exist_ok=True)

# =========================
# Logging
# =========================
def log_result(log_filename, destination_address, amount, success, message=""):
    log_message = f"{datetime.utcnow()} - Transaction to {destination_address} for {amount} XLM: "
    log_message += "Success\n" if success else f"Failed - {message}\n"

    print(log_message)
    with open(log_filename, "a") as f:
        f.write(log_message)

# =========================
# Payment sender
# =========================
def send_payment(log_filename, destination_address, amount, min_gas_fee=100):
    try:
        account = server.load_account(DISTRIBUTOR_ADDRESS)

        base_fee = max(server.fetch_base_fee(), min_gas_fee)

        tx = (
            TransactionBuilder(
                source_account=account,
                network_passphrase=Network.PUBLIC_NETWORK_PASSPHRASE,
                base_fee=base_fee,
            )
            .append_payment_op(
                destination=destination_address,
                amount=str(amount),
                asset=Asset.native(),
            )
            .set_timeout(60)
            .build()
        )

        tx.sign(DISTRIBUTOR_KP)
        response = server.submit_transaction(tx)

        if response.get("successful"):
            log_result(log_filename, destination_address, amount, True)
        else:
            log_result(log_filename, destination_address, amount, False, str(response))

    except Exception as e:
        extras = getattr(e, "extras", {}) or {}
        codes = extras.get("result_codes", {}) if isinstance(extras, dict) else {}

        if getattr(e, "status", None) == 504:
            time.sleep(5)
            send_payment(log_filename, destination_address, amount)

        elif codes.get("transaction") == "tx_bad_seq":
            time.sleep(1)
            send_payment(log_filename, destination_address, amount)

        elif codes.get("transaction") == "tx_too_late":
            time.sleep(1)
            send_payment(log_filename, destination_address, amount)

        elif codes.get("transaction") == "tx_insufficient_fee":
            if min_gas_fee < 2000:
                time.sleep(1)
                send_payment(log_filename, destination_address, amount, min_gas_fee * 2)
            else:
                log_result(
                    log_filename,
                    destination_address,
                    amount,
                    False,
                    "Network busy: insufficient fee",
                )

        elif (
            codes.get("transaction") == "tx_failed"
            and codes.get("operations")
            and codes["operations"][0] == "op_underfunded"
        ):
            log_result(
                log_filename,
                destination_address,
                amount,
                False,
                "Insufficient XLM balance",
            )

        else:
            log_result(log_filename, destination_address, amount, False, str(e))

# =========================
# Payment handler
# =========================
def handle_payment(payment):
    if payment.get("type") != "payment":
        return

    if not payment.get("transaction_successful", False):
        return

    if payment.get("asset_type") != "native":
        return

    if payment.get("to") != DISTRIBUTOR_ADDRESS:
        return

    if payment.get("from") == DISTRIBUTOR_ADDRESS:
        return

    incoming = Decimal(payment.get("amount", "0"))
    if incoming < MIN_INCOMING_XLM:
        return

    send_amount = (incoming * SEND_PERCENT).quantize(
        Decimal("0.0000001"), rounding=ROUND_DOWN
    )

    if send_amount <= 0:
        return

    print(
        f"\n💰 {datetime.utcnow()} | Incoming {incoming} XLM "
        f"from {payment.get('from')}"
    )
    print(f"➡️  Sending 25% = {send_amount} XLM")

    log_filename = f"logs/log_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    send_payment(log_filename, RECEIVER_ADDRESS, send_amount)

# =========================
# Main loop
# =========================
def main():
    print("🚀 AQS 25% bot started")
    print(f"👂 Listening for incoming XLM to {DISTRIBUTOR_ADDRESS}")

    cursor = "now"
    print(f"⏱️  Cursor = {cursor}\n")

    while True:
        try:
            payments = server.payments().for_account(DISTRIBUTOR_ADDRESS).cursor(cursor)

            for payment in payments.stream():
                cursor = payment["paging_token"]
                save_cursor(cursor)
                handle_payment(payment)

        except Exception as e:
            print(f"⚠️ Stream error: {e}")
            time.sleep(5)

# =========================
if __name__ == "__main__":
    main()
