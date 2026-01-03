import configparser
import os
import sys
import time
from datetime import datetime
from stellar_sdk import Server, Keypair, TransactionBuilder, Network, Asset
import schedule

# Configuration
config = configparser.ConfigParser()
config.read('config.txt')

DISTRIBUTOR_SECRET_KEY = config['DEFAULT'].get('DISTRIBUTOR_SECRET_KEY', '')
INTERVAL_HOURS = float(config['DEFAULT'].get('INTERVAL_HOURS', 3))
RECEIVER_ADDRESS = config['DEFAULT'].get('RECEIVER_ADDRESS', '')

# Validation: Check if required configuration values are set
if DISTRIBUTOR_SECRET_KEY == '':
    print("ERROR: DISTRIBUTOR_SECRET_KEY is not set in config.txt. Please set it before running the bot.", file=sys.stderr)
    sys.exit(1)

if RECEIVER_ADDRESS == '':
    print("ERROR: RECEIVER_ADDRESS is not set in config.txt. Please set it before running the bot.", file=sys.stderr)
    sys.exit(1)

# Constants
NETWORK_PASSPHRASE = Network.PUBLIC_NETWORK_PASSPHRASE
HORIZON_URL = "https://horizon.stellar.org"

# Stellar SDK Setup
server = Server(HORIZON_URL)
distributor_keypair = Keypair.from_secret(DISTRIBUTOR_SECRET_KEY)

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

def get_distributor_balance():
    """Fetch the current balance of the distributor's account."""
    try:
        distributor_account = server.accounts().account_id(distributor_keypair.public_key).call()
        for balance in distributor_account['balances']:
            if balance['asset_type'] == 'native':  # Get native XLM balance
                return float(balance['balance'])
        raise Exception("No native balance found in the account.")
    except Exception as e:
        print(f"Error fetching distributor balance: {e}")
        return 0  # Return 0 if there's an issue

def send_payment(log_filename, destination_address, amount, min_gas_fee = 100):
    """Send native XLM to the specified receiver."""
    try:
        # Load the distributor's account
        distributor_account = server.load_account(distributor_keypair.public_key)

        # Fetch base fee and ensure it's at least 100
        base_fee = server.fetch_base_fee()
        base_fee = max(base_fee, min_gas_fee)

        # Build the transaction
        transaction = (
            TransactionBuilder(
                source_account=distributor_account,
                network_passphrase=NETWORK_PASSPHRASE,
                base_fee=base_fee
            )
            .append_payment_op(
                destination=destination_address,
                amount=str(amount),
                asset=Asset.native()
            )
            .set_timeout(100)
            .build()
        )

        # Sign and submit the transaction
        transaction.sign(distributor_keypair)
        response = server.submit_transaction(transaction)

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
                print("Insufficient fee. Retrying with "+ str(2 * min_gas_fee) +" Stroops...")
                time.sleep(1)  # Brief delay before retrying
                send_payment(log_filename, destination_address, amount, 2 * min_gas_fee )
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
            error_message = f"Transaction failed: XLM amount is insufficient in distribution account."
            log_result(log_filename, destination_address, amount, False, error_message)        
        else:
            error_message = f"Transaction failed: {e}"
            log_result(log_filename, destination_address, amount, False, error_message)

def job():
    """Scheduled job to send payment."""
    try:
        # Create a log file with a timestamp
        log_filename = f"logs/log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"

        balance = get_distributor_balance()
        if balance <= 0:
            log_result(log_filename, RECEIVER_ADDRESS, 0, False, "Insufficient balance")
            return

        amount = round(balance * 0.25, 7)  # Calculate 25% of the balance, rounded to 7 decimals
        print(f"Starting transaction at {datetime.now()} with amount: {amount} XLM")
        send_payment(log_filename, RECEIVER_ADDRESS, amount)
    except Exception as e:
        print(f"An error occurred during the job: {e}")

# Schedule the job
schedule.every(INTERVAL_HOURS).hours.do(job)

# Run the job immediately
job()

# Keep the bot running
while True:
    schedule.run_pending()
    time.sleep(1)
