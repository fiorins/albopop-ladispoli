import io, re, requests, time, mimetypes
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
from .helpers import BOX_CONFIG_JSON, ATTACHMENT_FOLDER_ID, HEADERS


def get_box_client():
    jwt_config = JWTConfig.from_config_file(config_file_path=BOX_CONFIG_JSON)
    auth = BoxJWTAuth(config=jwt_config)
    return BoxClient(auth=auth)


def get_box_items(client, folder_id="0"):
    result = client.folders.get_folder_items(folder_id, sort="DATE", direction="DESC")
    return list(result.entries)


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
    ext = mimetypes.guess_extension(content_type) or ".pdf"
    if "pkcs7" in content_type or url.lower().endswith(".p7m"):
        ext = ".p7m"

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


def upload_to_box_folder(client, attachments, registry, all_items):
    """
    Downloads each attachment and uploads it to a Box subfolder
    named after the entry registry (e.g. '2026-1084').
    Returns list of uploaded file IDs and links.
    """
    files_id = []

    folder_label = f"altri_allegati_[{registry}]"

    # Create or find the subfolder for this entry
    try:
        existing = next((item for item in all_items if item.name == folder_label), None)
        if existing:
            subfolder_id = existing.id
        else:
            subfolder = client.folders.create_folder(
                name=folder_label,
                parent=UploadFileAttributesParentField(id=f"{ATTACHMENT_FOLDER_ID}"),
            )
            subfolder_id = subfolder.id

            all_items.append(subfolder)  # update cache

    except Exception as e:
        # Folder may already exist — try to find it
        print(f"Folder creation error (may already exist): {str(e)[:80]}")
        subfolder_id = "0"

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
                subfolder_id,
                custom_label=custom_label,
            )
            if not box_file_id:
                continue

            files_id.append(box_file_id)

            # print(f"Uploaded extra attachment {index + 1}: {registry}")
            time.sleep(2)

        except Exception as e:
            error_msg = getattr(e, "message", str(e))
            print(f"Error uploading attachment {index + 1} for {registry}: {error_msg}")
            continue

    if files_id:
        print(f"Uploaded extra attachments ({len(files_id)} tot) for {registry}\n")
    return subfolder_id, files_id


def get_or_create_box_link(client, item_id, kind="file"):
    try:
        if kind == "file":
            item = client.shared_links_files.get_shared_link_for_file(
                item_id, "shared_link"
            )

            if not item.shared_link:
                item = client.shared_links_files.add_share_link_to_file(
                    item_id,
                    "shared_link",
                    shared_link=AddShareLinkToFileSharedLink(
                        access=AddShareLinkToFileSharedLinkAccessField.OPEN
                    ),
                )

            return item.shared_link.download_url

        if kind == "folder":
            item = client.shared_links_folders.get_shared_link_for_folder(
                item_id, "shared_link"
            )

            if not item.shared_link:
                item = client.shared_links_folders.add_share_link_to_folder(
                    item_id,
                    "shared_link",
                    shared_link=AddShareLinkToFolderSharedLink(
                        access=AddShareLinkToFolderSharedLinkAccessField.OPEN
                    ),
                )

            return item.shared_link.url

        raise ValueError(f"Unsupported Box link kind: {kind}")

    except Exception as e:
        error_msg = getattr(e, "message", str(e))
        print(f"Box error getting/creating {kind} link: {error_msg}")
        return None
