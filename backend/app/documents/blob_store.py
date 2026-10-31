from contextlib import asynccontextmanager

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from app.config import settings

# The Azurite devstoreaccount1 key is the emulator's published, non-secret test key.
_AZURITE_CONNECTION = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://azurite:10000/devstoreaccount1;"
)


@asynccontextmanager
async def blob_container():
    if settings.azure_storage_connection_string:
        service = BlobServiceClient.from_connection_string(
            settings.azure_storage_connection_string, api_version=settings.azure_storage_api_version
        )
    elif settings.app_env == "development":
        service = BlobServiceClient.from_connection_string(
            _AZURITE_CONNECTION, api_version=settings.azure_storage_api_version
        )
    else:
        raise RuntimeError("Azure Blob credentials are not configured")
    async with service:
        container = service.get_container_client(settings.azure_blob_container)
        try:
            await container.create_container()
        except ResourceExistsError:
            pass
        yield container


async def upload_private_blob(blob_key: str, content: bytes, mime_type: str) -> None:
    async with blob_container() as container:
        blob = container.get_blob_client(blob_key)
        await blob.upload_blob(
            content, overwrite=False, content_settings=ContentSettings(content_type=mime_type)
        )


async def delete_blob(blob_key: str) -> None:
    async with blob_container() as container:
        try:
            await container.delete_blob(blob_key, delete_snapshots="include")
        except ResourceNotFoundError:
            pass


async def download_blob(blob_key: str):
    async with blob_container() as container:
        blob = container.get_blob_client(blob_key)
        response = await blob.download_blob()
        return response
