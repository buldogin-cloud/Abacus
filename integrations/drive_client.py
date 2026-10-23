"""
Клієнт Google Drive для читання та запису файлів
"""

import io
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload, MediaIoBaseUpload

from integrations.google_auth import get_credentials


class DriveClient:
    """Клієнт для роботи з Google Drive API"""
    
    def __init__(self):
        """Ініціалізація клієнта Drive"""
        creds = get_credentials()
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
    
    def find_file(self, name, parent_id=None, mime_type=None):
        """
        Знайти файл за назвою та (опціонально) ID папки
        
        Args:
            name: Назва файлу
            parent_id: ID батьківської папки (опціонально)
            mime_type: MIME тип файлу (опціонально)
            
        Returns:
            Словник з даними файлу або None
        """
        query_parts = [f"name = '{name}'", "trashed=false"]
        
        if parent_id:
            query_parts.append(f"'{parent_id}' in parents")
        if mime_type:
            query_parts.append(f"mimeType = '{mime_type}'")
        
        query = " and ".join(query_parts)
        
        try:
            results = self.service.files().list(
                q=query,
                fields='nextPageToken, files(id, name, mimeType, parents, modifiedTime)',
                pageSize=2
            ).execute()
        except Exception:
            # A failed lookup must never be treated as absence and create a duplicate.
            # Provider exceptions can contain URLs, payloads or credential material.
            raise RuntimeError("Drive filename lookup failed") from None
        files = results.get('files', [])
        if len(files) > 1 or results.get('nextPageToken'):
            raise RuntimeError("Ambiguous Drive filename; use a verified file ID")
        return files[0] if files else None

    def read_file(self, file_id):
        """
        Прочитати вміст текстового файлу
        
        Args:
            file_id: ID файлу в Drive
            
        Returns:
            Текстовий вміст файлу
        """
        try:
            request = self.service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            
            done = False
            while not done:
                status, done = downloader.next_chunk()
            
            fh.seek(0)
            return fh.read().decode('utf-8')
        except Exception as e:
            print(f"❌ Помилка читання файлу: {type(e).__name__}")
            return None
    
    def write_file(self, content, filename, parent_id, mime_type='text/markdown'):
        """
        Записати або оновити файл у Drive
        
        Args:
            content: Текстовий вміст
            filename: Назва файлу
            parent_id: ID батьківської папки
            mime_type: MIME тип файлу
            
        Returns:
            ID створеного/оновленого файлу
        """
        try:
            # Перевіряємо чи файл вже існує
            existing = self.find_file(filename, parent_id)
            
            file_metadata = {
                'name': filename,
                'parents': [parent_id]
            }
            
            media = MediaIoBaseUpload(
                io.BytesIO(content.encode('utf-8')),
                mimetype=mime_type,
                resumable=True
            )
            
            if existing:
                # Оновлюємо існуючий файл
                file = self.service.files().update(
                    fileId=existing['id'],
                    media_body=media
                ).execute()
            else:
                # Створюємо новий файл
                file_metadata['mimeType'] = mime_type
                file = self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                ).execute()
            
            return file.get('id')
        except Exception as e:
            print(f"❌ Помилка запису файлу: {type(e).__name__}")
            return None

    def update_file_by_id(self, file_id, content, mime_type='text/markdown'):
        """
        Оновити вміст існуючого файлу за ЯВНИМ ID (без пошуку за назвою).

        Використовується для канонічних файлів Command Center (Req 2):
        checkpoints.md / automation_log.md / handoff_runs.jsonl — щоб гарантовано
        писати в один і той самий файл, а не створювати паралельні копії.

        Args:
            file_id: ID існуючого файлу в Drive
            content: Текстовий вміст
            mime_type: MIME тип файлу

        Returns:
            ID файлу при успіху, або None при помилці
        """
        try:
            media = MediaIoBaseUpload(
                io.BytesIO(content.encode('utf-8')),
                mimetype=mime_type,
                resumable=True
            )
            file = self.service.files().update(
                fileId=file_id,
                media_body=media
            ).execute()
            return file.get('id', file_id)
        except Exception as e:
            print(f"❌ Помилка оновлення файлу: {type(e).__name__}")
            return None

    def trash_file(self, file_id):
        """
        Перемістити файл у кошик (м'яке видалення) за ID.

        Використовується одноразово для прибирання паралельних копій
        checkpoints/журналів у теці handoffs (Req 2).
        """
        try:
            self.service.files().update(
                fileId=file_id,
                body={'trashed': True}
            ).execute()
            return True
        except Exception as e:
            print(f"❌ Помилка переміщення у кошик: {type(e).__name__}")
            return False

    def delete_file(self, file_id):
        """
        Видалити файл з Drive
        
        Args:
            file_id: ID файлу
            
        Returns:
            True якщо успішно
        """
        try:
            self.service.files().delete(fileId=file_id).execute()
            return True
        except Exception as e:
            print(f"❌ Помилка видалення файлу: {type(e).__name__}")
            return False

