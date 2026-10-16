#haldes the downloading of the model

import logging
import os, shutil, hashlib, requests, time

logger = logging.getLogger(__name__)


class DownloadCancelled(Exception):
    pass


def download_model(url, dest_path, expected_sha_256=None, expected_size=None, progress_cb=None, max_retries=None):
    dest_path = str(dest_path)
    part_path = dest_path + ".part"
    max_retries = max_retries or 3
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    logger.info("Starting model download to %s", dest_path)

    if expected_size:
        free = shutil.disk_usage(os.path.dirname(dest_path)).free
        if free < expected_size * 1.5:
            logger.error("Insufficient disk space for model download to %s", dest_path)
            raise RuntimeError("Not enough free disk space for this model")
        
    for attempt in range(max_retries):
        try:
            resume_from = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            logger.info("Model download attempt %d, resuming at %d bytes", attempt + 1, resume_from)
            headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
            with requests.get(url, headers=headers, stream=True, timeout=30) as r:
                r.raise_for_status()
                if resume_from and r.status_code != 206:
                    os.remove(part_path)
                    resume_from = 0
                    raise requests.ConnectionError("Download server does not support resume")
                total = resume_from + int(r.headers.get("content-length", 0))
                downloaded = resume_from
                with open(part_path, "ab" if resume_from else "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb(downloaded, total)
            break
        except DownloadCancelled:
            if os.path.exists(part_path):
                os.remove(part_path)
            raise
        except (requests.ConnectionError, requests.Timeout):
            logger.warning("Model download attempt %d failed", attempt + 1)
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt) #back off than resume from where it left off

    if expected_size and os.path.getsize(part_path) != expected_size:
        logger.error("Downloaded model size did not match expected size")
        os.remove(part_path)
        raise RuntimeError("Downloaded model size is incorrect")
    if expected_sha_256 and _sha256(part_path) != expected_sha_256.lower():
        logger.error("Checksum validation failed for %s", part_path)
        os.remove(part_path)
        raise RuntimeError("Checksum mismatch - file corrupted, download aborted")
    os.replace(part_path, dest_path) #only now does it exist as a real candidate
    logger.info("Model download completed: %s", dest_path)
    return dest_path

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()