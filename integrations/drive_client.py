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
                fields='files(id, name, mimeType, parents, modifiedTime)',
                pageSize=1
            ).execute()
            
            files = results.get('files', [])
            return files[0] if files else None
        except Exception as e:
            print(f"❌ Помилка пошуку файлу: {e}")
            return None
    
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
            print(f"❌ Помилка читання файлу: {e}")
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
            print(f"❌ Помилка запису файлу: {e}")
            return None

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
            print(f"❌ Помилка видалення файлу: {e}")
            return False
