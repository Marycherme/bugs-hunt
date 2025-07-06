import os
import time
import logging
import json
from typing import Dict, Any, Optional, Set

import requests
from web3 import Web3
from web3.contract import Contract
from web3.logs import DISCARD
from web3.exceptions import ContractLogicError, TransactionNotFound
from dotenv import load_dotenv

# --- Configuration Setup ---
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(module)s.%(funcName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class Config:
    """A class to manage and provide access to application configuration."""
    SOURCE_CHAIN_RPC_URL: str = os.getenv("SOURCE_CHAIN_RPC_URL", "")
    BRIDGE_CONTRACT_ADDRESS: str = os.getenv("BRIDGE_CONTRACT_ADDRESS", "")
    RELAYER_API_ENDPOINT: str = os.getenv("RELAYER_API_ENDPOINT", "https://httpbin.org/post")
    POLLING_INTERVAL_SECONDS: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "15"))
    START_BLOCK_OFFSET: int = int(os.getenv("START_BLOCK_OFFSET", "100"))

    @staticmethod
    def validate() -> None:
        """Validates that essential configuration variables are set."""
        if not all([Config.SOURCE_CHAIN_RPC_URL, Config.BRIDGE_CONTRACT_ADDRESS]):
            raise ValueError("Missing required environment variables: SOURCE_CHAIN_RPC_URL, BRIDGE_CONTRACT_ADDRESS")
        logging.info("Configuration validated successfully.")


# --- Minimal Bridge Contract ABI ---
# This is a simplified ABI for demonstration purposes.
# It only contains the event we are interested in.
BRIDGE_CONTRACT_ABI = json.loads('''
[
    {
        "anonymous": false,
        "inputs": [
            {
                "indexed": true,
                "internalType": "address",
                "name": "sender",
                "type": "address"
            },
            {
                "indexed": false,
                "internalType": "uint256",
                "name": "destinationChainId",
                "type": "uint256"
            },
            {
                "indexed": true,
                "internalType": "address",
                "name": "recipient",
                "type": "address"
            },
            {
                "indexed": false,
                "internalType": "uint256",
                "name": "amount",
                "type": "uint256"
            },
            {
                "indexed": true,
                "internalType": "bytes32",
                "name": "transactionId",
                "type": "bytes32"
            }
        ],
        "name": "TokensLocked",
        "type": "event"
    }
]
''')


class BlockchainConnector:
    """
    Manages the connection to a single blockchain node.
    Encapsulates the Web3 instance and provides helper methods.
    """

    def __init__(self, rpc_url: str):
        """Initializes the connector with a given RPC URL."""
        self.rpc_url = rpc_url
        self.web3: Optional[Web3] = None
        self.connect()

    def connect(self) -> None:
        """Establishes connection to the blockchain node."""
        try:
            self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if not self.is_connected():
                raise ConnectionError("Failed to connect to the node initially.")
            logging.info(f"Successfully connected to blockchain node at {self.rpc_url}. Chain ID: {self.web3.eth.chain_id}")
        except Exception as e:
            logging.error(f"Error connecting to blockchain node: {e}")
            self.web3 = None

    def is_connected(self) -> bool:
        """Checks if the connection to the node is active."""
        return self.web3 is not None and self.web3.is_connected()

    def get_contract(self, address: str, abi: Dict[str, Any]) -> Optional[Contract]:
        """Returns a Web3 contract instance."""
        if not self.is_connected():
            logging.warning("Not connected to blockchain. Cannot get contract.")
            return None
        try:
            checksum_address = self.web3.to_checksum_address(address)
            return self.web3.eth.contract(address=checksum_address, abi=abi)
        except ValueError as e:
            logging.error(f"Invalid address or ABI provided: {e}")
            return None

    def get_latest_block_number(self) -> int:
        """Fetches the latest block number from the connected node."""
        if not self.is_connected():
            logging.warning("Not connected to blockchain. Returning 0 for block number.")
            return 0
        return self.web3.eth.block_number


class StateManager:
    """
    Manages the state of processed events to prevent duplicates.
    In a production system, this would be backed by a persistent database (e.g., Redis, PostgreSQL).
    """

    def __init__(self):
        self._processed_transaction_ids: Set[str] = set()
        logging.info("In-memory StateManager initialized.")

    def is_processed(self, tx_id: str) -> bool:
        """Checks if a transaction ID has already been processed."""
        return tx_id in self._processed_transaction_ids

    def mark_as_processed(self, tx_id: str) -> None:
        """Marks a transaction ID as processed."""
        self._processed_transaction_ids.add(tx_id)
        logging.debug(f"Transaction ID {tx_id} marked as processed.")


class TransactionRelayer:
    """
    Responsible for relaying the processed event data to an off-chain service.
    This simulates notifying a separate relayer component that would sign and submit
    the transaction on the destination chain.
    """

    def __init__(self, api_endpoint: str):
        self.api_endpoint = api_endpoint
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "User-Agent": "BugsHunt-Bridge-Listener/1.0"})

    def relay_transaction_data(self, event_data: Dict[str, Any]) -> bool:
        """
        Sends the event data to the configured relayer API endpoint.
        Includes basic retry logic.
        """
        payload = {
            "sourceTransactionId": event_data['transactionId'],
            "destinationChainId": event_data['destinationChainId'],
            "recipient": event_data['recipient'],
            "amount": event_data['amount'],
            "sourceTransactionHash": event_data['transactionHash']
        }

        logging.info(f"Relaying data for TxID: {payload['sourceTransactionId']}")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.post(self.api_endpoint, json=payload, timeout=10)
                response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)
                logging.info(f"Successfully relayed TxID {payload['sourceTransactionId']}. API Response: {response.status_code}")
                # logging.debug(f"Relayer API response body: {response.text}")
                return True
            except requests.exceptions.RequestException as e:
                logging.warning(f"Attempt {attempt + 1}/{max_retries} failed to relay TxID {payload['sourceTransactionId']}. Error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    logging.error(f"Failed to relay TxID {payload['sourceTransactionId']} after {max_retries} attempts.")
                    return False
        return False


class CrossChainEventListener:
    """
    The core component of the service. It connects to the source chain,
    listens for specific events, and coordinates processing and relaying.
    """

    def __init__(self, config: Config):
        self.config = config
        self.connector = BlockchainConnector(config.SOURCE_CHAIN_RPC_URL)
        self.state_manager = StateManager()
        self.relayer = TransactionRelayer(config.RELAYER_API_ENDPOINT)
        self.bridge_contract: Optional[Contract] = None

    def _initialize_contract(self) -> bool:
        """Initializes the bridge contract instance."""
        if not self.connector.is_connected():
            logging.error("Cannot initialize contract, no blockchain connection.")
            return False
        
        self.bridge_contract = self.connector.get_contract(
            address=self.config.BRIDGE_CONTRACT_ADDRESS,
            abi=BRIDGE_CONTRACT_ABI
        )
        if self.bridge_contract is None:
            logging.critical("Failed to initialize bridge contract. Shutting down.")
            return False

        logging.info(f"Bridge contract initialized at address: {self.bridge_contract.address}")
        return True

    def _process_event(self, event: Dict[str, Any]) -> None:
        """Processes a single event log."""
        try:
            tx_id_hex = event['args']['transactionId'].hex()
            logging.info(f"New 'TokensLocked' event detected. Tx Hash: {event['transactionHash'].hex()}, TxID: {tx_id_hex}")

            if self.state_manager.is_processed(tx_id_hex):
                logging.warning(f"Event with TxID {tx_id_hex} has already been processed. Skipping.")
                return

            # Prepare data for the relayer
            event_data = {
                'transactionId': tx_id_hex,
                'destinationChainId': event['args']['destinationChainId'],
                'recipient': event['args']['recipient'],
                'amount': str(event['args']['amount']), # Convert to string for JSON compatibility
                'transactionHash': event['transactionHash'].hex(),
                'blockNumber': event['blockNumber']
            }
            
            # Relay and update state
            if self.relayer.relay_transaction_data(event_data):
                self.state_manager.mark_as_processed(tx_id_hex)

        except Exception as e:
            logging.error(f"An unexpected error occurred during event processing: {e}", exc_info=True)

    def run(self) -> None:
        """
        Starts the main event listening loop.
        Polls the blockchain for new events at a configured interval.
        """
        if not self._initialize_contract():
            return

        latest_block = self.connector.get_latest_block_number()
        from_block = max(0, latest_block - self.config.START_BLOCK_OFFSET)

        logging.info(f"Starting event listener loop. Polling every {self.config.POLLING_INTERVAL_SECONDS} seconds.")
        logging.info(f"Initial scan from block: {from_block}")

        while True:
            try:
                if not self.connector.is_connected():
                    logging.warning("Connection lost. Attempting to reconnect...")
                    self.connector.connect()
                    time.sleep(5)
                    continue

                to_block = self.connector.get_latest_block_number()
                if from_block > to_block:
                    logging.info(f"No new blocks since last poll (from_block: {from_block}, to_block: {to_block}). Waiting...")
                    time.sleep(self.config.POLLING_INTERVAL_SECONDS)
                    continue

                event_filter = self.bridge_contract.events.TokensLocked.create_filter(
                    fromBlock=from_block,
                    toBlock=to_block
                )
                
                events = event_filter.get_all_entries()
                if events:
                    logging.info(f"Found {len(events)} new event(s) between blocks {from_block} and {to_block}.")
                    for event in events:
                        self._process_event(event)
                
                # Update the starting block for the next iteration to avoid reprocessing
                from_block = to_block + 1

            except Exception as e:
                logging.error(f"An error occurred in the main loop: {e}", exc_info=True)
                # In case of a severe error, wait longer before retrying
                time.sleep(60)
            
            time.sleep(self.config.POLLING_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        Config.validate()
        listener = CrossChainEventListener(Config())
        listener.run()
    except ValueError as e:
        logging.critical(f"Configuration error: {e}")
    except KeyboardInterrupt:
        logging.info("Service shutting down gracefully.")
    except Exception as e:
        logging.critical(f"An unhandled critical error occurred: {e}", exc_info=True)
