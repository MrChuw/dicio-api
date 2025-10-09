import json
import logging
import random
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from itertools import chain
from pathlib import Path

import hunspell
import uvicorn
from aiohttp_client_cache import CachedSession, SQLiteBackend
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from custom_logging import CustomizeLogger

CACHE_DIR = Path(".cache/dictionaries")

logger = logging.getLogger(__name__)
config_path = Path(__file__).with_name("logging_config.json")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    app.client_cache = SQLiteBackend(
        cache_name=f".cache/aiohttp-dicio.db",
        urls_expire_after={
            "https://raw.githubusercontent.com/*": timedelta(days=10),
        },
        allowed_methods=("GET",),
        include_headers=True,
        allowed_codes=(200,),
    )
    _app.client_session = CachedSession(cache=app.client_cache)
    yield
    await _app.client_session.close()


class Annoyed(FastAPI):
    client_session: CachedSession
    client_cache: SQLiteBackend


app = Annoyed(lifespan=lifespan, debug=False)
app.logger = CustomizeLogger.make_logger(config_path)


@app.get("/favicon.ico", response_class=StreamingResponse)
async def get_favicon():
    images_txt = CACHE_DIR.parent / "images.txt"
    with open(images_txt, "r") as file:
        lines = file.readlines()
    image = random.choice(lines).replace("\n", "")
    with open(image, "rb") as image_file:
        image_io = [image_file.read()]
    return StreamingResponse(image_io, media_type="image/png")


async def clone_or_update_repo() -> bool:
    url = "https://github.com/LibreOffice/dictionaries.git"
    last_file = CACHE_DIR.parent / ".last_update"
    cd, now = 6 * 3600, time.time()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(CACHE_DIR), *args],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    if not CACHE_DIR.exists():
        CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Cloning repo...")
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(CACHE_DIR)], check=True
        )
        last_file.write_text(str(now))
        return True

    if last_file.exists() and now - float(last_file.read_text() or 0) < cd:
        return False

    logger.info("Pulling updates...")
    before, after = git("rev-parse", "HEAD"), (git("pull"), git("rev-parse", "HEAD"))[1]
    updated = before != after
    logger.info("Updated." if updated else "Already up to date.")
    last_file.write_text(str(now))
    return updated


async def get_languages():
    updated = await clone_or_update_repo()
    base_dir = CACHE_DIR.parent
    languages_json = base_dir / "languages.json"
    images_txt = base_dir / "images.txt"
    languages_json.touch()
    images_txt.touch()

    if updated:
        return json.load(open(languages_json, "r+", encoding="utf-8"))

    await app.client_cache.clear()

    result = {}
    image_paths: list[str] = []

    for lang_dir in CACHE_DIR.iterdir():
        if not lang_dir.is_dir():
            continue

        variants = []
        for dic_file in lang_dir.glob("*.dic"):
            aff_file = dic_file.with_suffix(".aff")
            if aff_file.exists():
                variants.append(dic_file.stem)

        for img in chain(
            lang_dir.glob("*.png"),
            lang_dir.glob("*.jpg"),
            lang_dir.glob("*.jpeg"),
            lang_dir.glob("*.gif"),
        ):
            image_paths.append(str(img.resolve()))

        if variants:
            result[lang_dir.name] = variants

    with open(languages_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    with open(images_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(image_paths))

    return result


@app.get("/languages")
async def languages():
    data = await get_languages()
    return JSONResponse(content=data)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return HTMLResponse(
        """
    <html>
        <body style="font-family: sans-serif; padding: 2em;">
            <h2>Hunspell Dictionary API</h2>
            <p>
                This is a simple API that uses
                <a href="https://github.com/LibreOffice/dictionaries" target="_blank">
                    LibreOffice dictionaries
                </a>
                to verify if a given word exists in a specific language and variation.
            </p>
            <p>
                To check supported languages, go to
                <a href="/languages">/languages</a>.
            </p>
        </body>
    </html>
    """
    )


@app.exception_handler(StarletteHTTPException)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return JSONResponse(
            status_code=404,
            content={"error": {"Supported": await get_languages()}},
        )
    return await request.app.default_exception_handler(request, exc)


@app.get("/{lang}/{file}/{word}", response_class=JSONResponse)
async def get_word(request: Request, lang: str, file: str, word: str):
    dic_path = Path(CACHE_DIR / f"{lang}/{file}.dic")
    aff_path = Path(CACHE_DIR / f"{lang}/{file}.aff")
    try:
        hob_dict = hunspell.HunSpell(str(dic_path), str(aff_path))
    except Exception as e:
        logger.error(e)
        return await custom_404_handler(request, StarletteHTTPException(404))
    response = {
        "exist": hob_dict.spell(word),
        "suggestions": hob_dict.suggest(word),
        "stem": [_word.decode("utf-8") for _word in hob_dict.stem(word)],
        "analyze": [_word.decode("utf-8") for _word in hob_dict.analyze(word)],
    }

    return JSONResponse(response, status_code=200)


if __name__ == "__main__":
    # uvicorn.run("main:app", host="0.0.0.0", port=12834, log_level="info", reload=True)
    uvicorn.run("main:app", host="0.0.0.0", port=12834, log_level="info", workers=4)
