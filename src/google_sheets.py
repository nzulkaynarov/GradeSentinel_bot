import os
import json
import time
import logging
from typing import List, Optional
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import socket
import urllib.error

logger = logging.getLogger(__name__)

# Путь к файлу ключа
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
CREDENTIALS_FILE = os.path.join(CONFIG_DIR, "credentials.json")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

_sheets_service = None

def get_sheets_service():
    """Инициализирует и возвращает кэшированный сервис Google Sheets API (singleton)."""
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service

    if not os.path.exists(CREDENTIALS_FILE):
        return None

    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES)
    _sheets_service = build('sheets', 'v4', credentials=creds)
    return _sheets_service

def get_sheet_data(spreadsheet_id: str, range_name: str, max_retries: int = 3) -> Optional[List[List[str]]]:
    """
    Получает данные из заданного диапазона Google Таблицы с поддержкой Exponential Backoff.
    """
    service = get_sheets_service()
    if not service:
        logger.error(f"Credentials file not found at {CREDENTIALS_FILE}")
        return None
        
    for attempt in range(max_retries):
        try:
            sheet = service.spreadsheets()
            result = sheet.values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
            return result.get('values', [])
        except HttpError as err:
            if err.resp.status == 429:
                wait_time = (2 ** attempt) + 1
                # Тег [GOOGLE_QUOTA] нужен для отдельного грепа в логах:
                # помогает увидеть, упёрлись ли в Sheets quota (300 read/min/user).
                logger.warning(
                    f"[GOOGLE_QUOTA] Sheets API 429 for {spreadsheet_id}. "
                    f"Retrying in {wait_time}s (attempt {attempt+1}/{max_retries})"
                )
                time.sleep(wait_time)
            elif err.resp.status == 503:
                wait_time = (2 ** attempt) + 1
                logger.warning(
                    f"Google Sheets API 503 (Service Unavailable) for {spreadsheet_id}. "
                    f"Retrying in {wait_time}s (attempt {attempt+1}/{max_retries})"
                )
                time.sleep(wait_time)
            else:
                logger.error(f"Google API Error fetching data: {err}")
                return None
        except (socket.error, urllib.error.URLError) as e:
            wait_time = (2 ** attempt) + 1
            logger.warning(f"Network error ({e}). Retrying in {wait_time}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait_time)

    logger.error(f"Max retries exceeded while fetching data for {spreadsheet_id}")
    return None

def get_spreadsheet_title(spreadsheet_id: str, max_retries: int = 3) -> Optional[str]:
    """Получает название (заголовок) Google Таблицы с поддержкой Exponential Backoff."""
    service = get_sheets_service()
    if not service:
        return None

    for attempt in range(max_retries):
        try:
            spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            return spreadsheet.get('properties', {}).get('title')
        except HttpError as err:
            if err.resp.status == 429:
                wait_time = (2 ** attempt) + 1
                logger.warning(
                    f"[GOOGLE_QUOTA] Sheets API 429 for title of {spreadsheet_id}. "
                    f"Retrying in {wait_time}s (attempt {attempt+1}/{max_retries})"
                )
                time.sleep(wait_time)
            elif err.resp.status == 503:
                wait_time = (2 ** attempt) + 1
                logger.warning(
                    f"Google Sheets API 503 fetching title. "
                    f"Retrying in {wait_time}s (attempt {attempt+1}/{max_retries})"
                )
                time.sleep(wait_time)
            else:
                logger.error(f"Google API Error fetching title: {err}")
                return None
        except (socket.error, urllib.error.URLError) as e:
            wait_time = (2 ** attempt) + 1
            logger.warning(f"Network error ({e}). Retrying in {wait_time}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait_time)

    logger.error(f"Max retries exceeded while fetching title for {spreadsheet_id}")
    return None
