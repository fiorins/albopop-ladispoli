import io, re, os, requests, time
from dotenv import load_dotenv

from box_sdk_gen import (
    AddShareLinkToFolderSharedLink,
    AddShareLinkToFolderSharedLinkAccessField,
    BoxClient,
    BoxJWTAuth,
    JWTConfig,
    UploadFileAttributes,
    UploadFileAttributesParentField,
    AddShareLinkToFileSharedLink,
    AddShareLinkToFileSharedLinkAccessField,
)

load_dotenv()


# ── Configs ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOX_CONFIG_JSON = os.path.join(BASE_DIR, "..", ".secrets", "config_box.json")

HEADERS = {"User-Agent": "Mozilla/5.0"}


# ── BOX cloud ─────────────────────────────────────────────────────────────────
def get_box_client():
    jwt_config = JWTConfig.from_config_file(config_file_path=BOX_CONFIG_JSON)
    auth = BoxJWTAuth(config=jwt_config)
    return BoxClient(auth=auth)


def get_box_items(client, folder_id="0"):
    result = client.folders.get_folder_items(folder_id, sort="DATE", direction="DESC")
    return [entry.name for entry in result.entries]


# grab a direct file url and upload it on Box
# returns the id of the Box file object if the upload is successful, or None if it fails.
def upload_to_box(client, url, registry, folder_id="0", custom_label=None):

    # 1. Download the file
    # Retry download up to 3 times
    file_resp = None
    for attempt in range(1, 4):
        try:
            file_resp = requests.get(url, headers=HEADERS, timeout=30)
            file_resp.raise_for_status()

            if (
                not file_resp.content.startswith(b"%PDF")
                and b"<html" in file_resp.content[:100].lower()
            ):
                raise ValueError("Response is HTML, not a valid file")

            break
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"Download attempt {attempt}/3 failed for {registry}: {e}")
            if attempt == 3:
                return None, None
            time.sleep(5 * attempt)  # 5s, 10s, 15s

    if file_resp is None:
        return None, None

    # 2. Determine the correct extension
    content_type = file_resp.headers.get("Content-Type", "").split(";")[0].lower()

    # Mapping for common types in Albo Pretorio
    if "pdf" in content_type:
        ext = ".pdf"
    elif "word" in content_type or "msword" in content_type:
        ext = ".docx"
    elif (
        "pkcs7" in content_type or "p7m" in content_type or url.lower().endswith(".p7m")
    ):
        ext = ".p7m"
    else:
        # Fallback: try to guess from the URL itself
        guessed_ext = os.path.splitext(url.split("?")[0])[1]
        ext = guessed_ext if guessed_ext else ".pdf"

    # 3. Use custom label or auto-generate
    file_label = custom_label if custom_label else f"allegato_atto_[{registry}]{ext}"

    # 4. Upload to Box
    try:
        uploaded = client.uploads.upload_file(
            attributes=UploadFileAttributes(
                name=file_label,
                parent=UploadFileAttributesParentField(id=folder_id),
            ),
            file=io.BytesIO(file_resp.content),
        )
        file = uploaded.entries[0]
        print(f"Uploaded to Box: {registry}")
        return file.id, file_resp.content

    except Exception as e:
        error_msg = getattr(e, "message", str(e))
        print(f"Box error uploading {registry}: {error_msg}")
        return None, None


def upload_to_box_folder(client, attachments, registry):
    """
    Downloads each attachment and uploads it to a Box subfolder
    named after the entry registry (e.g. '2026-1084').
    Returns list of uploaded file IDs and links.
    """
    files_id = []

    folder_label = f"altri_allegati_[{registry}]"

    # Create or find the subfolder for this entry
    try:
        items = client.folders.get_folder_items("0")
        existing = next(
            (item for item in items.entries if item.name == folder_label), None
        )
        if existing:
            folder_id = existing.id
        else:
            subfolder = client.folders.create_folder(
                name=folder_label,
                parent=UploadFileAttributesParentField(id="0"),
            )
            folder_id = subfolder.id

    except Exception as e:
        # Folder may already exist — try to find it
        print(f"Folder creation error (may already exist): {str(e)[:80]}")
        folder_id = "0"

    for index, attachment in enumerate(attachments):
        try:
            # Use sanitized original filename from the page, with index suffix
            base_name = attachment["filename"].rsplit(".", 1)[0]  # strip extension
            sanitized = re.sub(r"[^\w\s\-]", "", base_name).strip().replace(" ", "_")
            custom_label = f"{sanitized}_{index + 1}.pdf"

            box_file_id, _ = upload_to_box(
                client,
                attachment["url"],
                registry,
                folder_id,
                custom_label=custom_label,
            )
            if not box_file_id:
                continue

            files_id.append(box_file_id)

            print(f"Uploaded extra attachment {index + 1}: {registry}")
            time.sleep(2)

        except Exception as e:
            error_msg = getattr(e, "message", str(e))
            print(f"Error uploading attachment {index + 1} for {registry}: {error_msg}")
            continue

    return folder_id, files_id


def get_or_create_file_link(client, file_id):
    # Try to create the shared url
    try:
        file = client.shared_links_files.get_shared_link_for_file(
            file_id, "shared_link"
        )

        if not file.shared_link:
            file = client.shared_links_files.add_share_link_to_file(
                file_id,
                "shared_link",
                shared_link=AddShareLinkToFileSharedLink(
                    access=AddShareLinkToFileSharedLinkAccessField.OPEN
                ),
            )

        return file.shared_link.download_url

    # Fallback: recover
    except Exception as e:
        error_msg = getattr(e, "message", str(e))
        print(f"Box error getting/creating link: {error_msg}")
        return None


def get_or_create_folder_link(client, folder_id):
    # Try to create the shared url
    try:
        folder = client.shared_links_folders.get_shared_link_for_folder(
            folder_id, "shared_link"
        )

        if not folder.shared_link:
            folder = client.shared_links_folders.add_share_link_to_folder(
                folder_id,
                "shared_link",
                shared_link=AddShareLinkToFolderSharedLink(
                    access=AddShareLinkToFolderSharedLinkAccessField.OPEN
                ),
            )

        return folder.shared_link.url

    # Fallback: recover
    except Exception as e:
        error_msg = getattr(e, "message", str(e))
        print(f"Box error getting/creating folder: {error_msg}")
        return None
