import configparser
import os
import sys
import time
from datetime import datetime
from stellar_sdk import Server, Keypair, TransactionBuilder, Network, Asset

# Configuration
config = configparser.ConfigParser()
config.read('config.txt')

DISTRIBUTOR_SECRET_KEY = config['DEFAULT'].get('DISTRIBUTOR_SECRET_KEY', '')
RECEIVER_ADDRESS = config['DEFAULT'].get('RECEIVER_ADDRESS', '')

# Validation: Check if required configuration values are set
if DISTRIBUTOR_SECRET_KEY == '':
    print("ERROR: DISTRIBUTOR_SECRET_KEY is not set in config.txt. Please set it before running the bot.", file=sys.stderr)
    sys.exit(1)

if RECEIVER_ADDRESS == '':
    print("ERROR: RECEIVER_ADDRESS is not set in config.txt. Please set it before running the bot.", file=sys.stderr)
    sys.exit(1)

# Constants
HORIZON_URL = "https://horizon.stellar.org"

# Stellar SDK Setup
server = Server(HORIZON_URL)
DISTRIBUTOR_KP = Keypair.from_secret(DISTRIBUTOR_SECRET_KEY)
DISTRIBUTOR_ADDRESS = DISTRIBUTOR_KP.public_key

SEND_PERCENT = 0.25   # 25%
MIN_INCOMING_XLM = 0  # optional threshold

# Ensure the logs directory exists
os.makedirs('logs', exist_ok=True)

def log_result(log_filename, destination_address, amount, success, message=""):
    """
    Log the result of the transaction to a file.

    :param log_filename: Name of the log file
    :param destination_address: The recipient's Stellar account address
    :param amount: The amount of XLM sent
    :param success: Boolean indicating transaction success
    :param message: Optional message for additional details
    """
    log_message = f"{datetime.now()} - Transaction to {destination_address} for {amount} XLM: "
    log_message += "Success\n" if success else f"Failed - {message}\n"

    print(log_message)
    with open(log_filename, 'a') as log_file:
        log_file.write(log_message)

print("🚀 AQS 25% bot started")
print(f"👂 Listening for NEW incoming XLM payments to {DISTRIBUTOR_ADDRESS}")
print("⏱️  Cursor = now (old transactions ignored)\n")

def send_payment(log_filename, destination_address, amount: float, min_gas_fee=100):
    """Send native XLM to the specified receiver."""
    try:
        # Load the distributor's account
        account = server.load_account(DISTRIBUTOR_ADDRESS)

        # Fetch base fee and ensure it's at least 100
        base_fee = server.fetch_base_fee()
        base_fee = max(base_fee, min_gas_fee)

        # Build the transaction
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

        # Sign and submit the transaction
        tx.sign(DISTRIBUTOR_KP)
        response = server.submit_transaction(tx)

        if response.get('successful', False):
            log_result(log_filename, destination_address, amount, True)
        else:
            log_result(log_filename, destination_address, amount, False, f"Transaction response: {response}")
    except Exception as e:
        if hasattr(e, 'status') and e.status == 504:
            print("504 Gateway Timeout. Retrying...")
            time.sleep(5)  # Delay before retrying
            send_payment(log_filename, destination_address, amount)
        elif (
            hasattr(e, 'extras') and 
            e.extras is not None and 
            isinstance(e.extras.get('result_codes'), dict) and 
            e.extras['result_codes'].get('transaction') == 'tx_bad_seq'
        ):
            print("Bad sequence number. Reloading account and retrying...")
            time.sleep(1)  # Brief delay before retrying
            send_payment(log_filename, destination_address, amount)
        elif (
            hasattr(e, 'extras') and 
            e.extras is not None and 
            isinstance(e.extras.get('result_codes'), dict) and 
            e.extras['result_codes'].get('transaction') == 'tx_too_late'
        ):
            print("Transaction time out. Retrying...")
            time.sleep(1)  # Brief delay before retrying
            send_payment(log_filename, destination_address, amount)
        elif (
            hasattr(e, 'extras') and 
            e.extras is not None and 
            isinstance(e.extras.get('result_codes'), dict) and 
            e.extras['result_codes'].get('transaction') == 'tx_insufficient_fee'
        ):
            if min_gas_fee < 2000:
                print(f"Insufficient fee. Retrying with {2 * min_gas_fee} Stroops...")
                time.sleep(1)  # Brief delay before retrying
                send_payment(log_filename, destination_address, amount, 2 * min_gas_fee)
            else:
                error_message = "Transaction Failed: Network is too busy at this time. Please try again this transaction at further time."
                log_result(log_filename, destination_address, amount, False, error_message)
        elif (
            hasattr(e, 'extras') and 
            e.extras is not None and 
            isinstance(e.extras.get('result_codes'), dict) and 
            e.extras['result_codes'].get('transaction') == 'tx_failed' and 
            e.extras['result_codes'].get('operations') and 
            len(e.extras['result_codes'].get('operations')) > 0 and
            e.extras['result_codes'].get('operations')[0] == "op_underfunded"
        ):            
            error_message = "Transaction failed: XLM amount is insufficient in distribution account."
            log_result(log_filename, destination_address, amount, False, error_message)
        else:
            error_message = f"Transaction failed: {e}"
            log_result(log_filename, destination_address, amount, False, error_message)

def handle_payment(payment):
    # Only payment ops
    if payment.get("type") != "payment":
        return

    # Only native XLM
    if payment.get("asset_type") != "native":
        return

    # Incoming only
    if payment.get("to") != DISTRIBUTOR_ADDRESS:
        return

    # Ignore self-payments
    if payment.get("from") == DISTRIBUTOR_ADDRESS:
        return

    incoming = float(payment.get("amount", "0"))
    if incoming < MIN_INCOMING_XLM:
        return

    send_amount = round(incoming * SEND_PERCENT, 7)
    if send_amount <= 0:
        return

    print(
        f"\n💰 {datetime.utcnow()} | Incoming {incoming} XLM "
        f"from {payment.get('from')}"
    )
    print(f"➡️  Sending 25% = {send_amount} XLM")

    # Create a log file with a timestamp
    log_filename = f"logs/log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"

    try:
        send_payment(log_filename, RECEIVER_ADDRESS, send_amount)
    except Exception as e:
        print(f"❌ Error sending payment: {e}")

def main():
    cursor = "now"  # 🔥 THIS IS THE KEY PART

    while True:
        try:
            payments = server.payments().for_account(DISTRIBUTOR_ADDRESS).cursor(cursor)
            for payment in payments.stream():
                handle_payment(payment)

        except Exception as e:
            print(f"⚠️ Stream error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()