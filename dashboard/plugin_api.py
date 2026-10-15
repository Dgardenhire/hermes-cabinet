"""Cabinet's page backend, mounted by Hermes at ``/api/plugins/bot-cabinet-v2/``.

Hermes imports this file as a loose module and mounts ``router`` when the plugin is enabled. Every
``/api/`` path already needs the dashboard session token, which the desktop's ``ctx.rest`` adds.
Routes are thin wrappers around ``api``, where the logic and the tests live. Nothing here creates a
profile: the desktop sends the reviewed request to Hermes's gateway (``profiles.create``) after you
confirm, then calls ``/install/confirm`` so Cabinet can check and record what Hermes made. Cabinet's own
record (its data folder) and the removal of a profile Cabinet made are the only writes on this side.
"""

import importlib
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

_PACKAGE_NAME = "bot_cabinet_v2_dashboard_package"


def _load_package():
    package = sys.modules.get(_PACKAGE_NAME)
    if package is None:
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(_PACKAGE_NAME, root / "__init__.py",
                                                      submodule_search_locations=[str(root)])
        package = importlib.util.module_from_spec(spec)
        package.__path__ = [str(root)]
        sys.modules[_PACKAGE_NAME] = package
        try:
            spec.loader.exec_module(package)
        except BaseException:
            sys.modules.pop(_PACKAGE_NAME, None)
            raise
    return package


_load_package()
api = importlib.import_module(f"{_PACKAGE_NAME}.api")
router = APIRouter()


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except api.ApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/fit")
def get_fit_form():
    return _call(api.fit_form)


@router.post("/fit")
def post_fit(body: dict):
    # ctx.rest sends the body as an object; FastAPI hands it over as a dict.
    return _call(api.fit_answer, body)


@router.get("/crew/{slug}")
def get_crew(slug: str):
    return _call(api.crew_plan, slug)


@router.post("/lab/start")
def post_lab_start(body: dict):
    return _call(api.lab_start, body)


@router.post("/lab/plan")
def post_lab_plan(body: dict):
    return _call(api.lab_plan, body)


# ---- the live catalog ----

@router.get("/catalog")
def get_catalog():
    return _call(api.catalog_view)


@router.post("/catalog/refresh")
def post_catalog_refresh():
    return _call(api.catalog_refresh)


# ---- add a Bot, set up a crew, keep a kit ----

@router.post("/bots/{slug}/plan")
def post_bot_plan(slug: str, body: dict):
    return _call(api.bot_plan, slug, body)


@router.post("/install/confirm")
def post_install_confirm(body: dict):
    return _call(api.install_confirm, body)


@router.post("/crews/{slug}/setup")
def post_crew_setup(slug: str, body: dict):
    return _call(api.crew_setup, slug, body)


@router.post("/crews/confirm")
def post_crew_confirm(body: dict):
    return _call(api.crew_confirm, body)


@router.get("/crews/{crew_id}/cards")
def get_crew_cards(crew_id: int):
    return _call(api.crew_cards_plan, crew_id)


@router.post("/crews/{crew_id}/cards")
def post_crew_cards(crew_id: int, body: dict):
    return _call(api.crew_cards_create, crew_id, body)


@router.post("/kits")
def post_kit(body: dict):
    return _call(api.kit_save, body)


@router.post("/kits/{kit_id}/plan")
def post_kit_plan(kit_id: int, body: dict):
    return _call(api.kit_plan, kit_id, body)


# ---- My bots and undo ----

@router.get("/mybots")
def get_mybots():
    return _call(api.my_bots)


@router.get("/remove/{kind}/{item_id}")
def get_remove_preview(kind: str, item_id: int):
    return _call(api.remove_preview, kind, item_id)


@router.post("/remove/{kind}/{item_id}")
def post_remove(kind: str, item_id: int, body: dict):
    return _call(api.remove, kind, item_id, body)


@router.post("/forget/{item_id}")
def post_forget(item_id: int):
    return _call(api.forget, item_id)
