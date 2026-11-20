"""Mounts the app_campaigns sub-server on upstream's server object."""

import importlib
import pkgutil

import ads_mcp.server as upstream_server
from ads_mcp.coordinator import mcp
from fastmcp import FastMCP

import ads_app_mcp.tools as tools_pkg
from ads_app_mcp.tools import app_campaigns_mcp

_mounted = False


def build_server() -> FastMCP:
    """Import every tool module and the prompts, mount once; return
    upstream's server."""
    global _mounted
    import ads_app_mcp.prompts  # noqa: F401  (registers on upstream's mcp)

    for _, name, _ in pkgutil.iter_modules(tools_pkg.__path__):
        importlib.import_module(f"ads_app_mcp.tools.{name}")
    if not _mounted:
        mcp.mount(app_campaigns_mcp, namespace="app_campaigns")
        _mounted = True
    return mcp


def run_server() -> None:
    build_server()
    upstream_server.run_server()
