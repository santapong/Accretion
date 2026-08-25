"""Connector definitions for external OAuth-backed services (v0.3 M2)."""

from accretion.connectors.github import GITHUB_CONNECTOR_ID, github_connector, github_endpoints

__all__ = ["GITHUB_CONNECTOR_ID", "github_connector", "github_endpoints"]
