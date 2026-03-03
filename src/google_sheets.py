import os
import json
from typing import List, Optional
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Путь к файлу ключа
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
CREDENTIALS_FILE = os.path.join(CONFIG_DIR, "credentials.json")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

def get_sheets_service():
    """Инициализирует и возвращает сервис Google Sheets API."""
    if not os.path.exists(CREDENTIALS_FILE):
        return None
        
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES)
    service = build('sheets', 'v4', credentials=creds)
    return service

def get_sheet_data(spreadsheet_id: str, range_name: str) -> Optional[List[List[str]]]:
    """
    Получает данные из заданного диапазона Google Таблицы.
    
    Args:
        spreadsheet_id (str): ID таблицы (из URL).
        range_name (str): Диапазон, например, 'Сегодня!A1:Z100'.
        
    Returns:
        list: Двумерный список значений ячеек, либо None в случае ошибки.
    """
    service = get_sheets_service()
    if not service:
        print(f"Error: Credentials file not found at {CREDENTIALS_FILE}")
        return None
        
    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=spreadsheet_id,
                                    range=range_name).execute()
        values = result.get('values', [])
        return values
    except HttpError as err:
        print(f"Google API Error: {err}")
        return None
