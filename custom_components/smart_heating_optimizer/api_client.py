"""API client for IoT Platform Smart Heating integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import (
    API_DASHBOARD,
    API_INSTALLATION,
    API_OPTIMIZE,
    API_REGISTER,
    API_TELEMETRY,
    API_VACATION,
    API_ZONES,
)

_LOGGER = logging.getLogger(__name__)


class SmartHeatingAPIError(Exception):
    """Base exception for API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        """Initialize the exception."""
        super().__init__(message)
        self.status_code = status_code


class SmartHeatingAuthError(SmartHeatingAPIError):
    """Authentication error."""


class SmartHeatingConnectionError(SmartHeatingAPIError):
    """Connection error."""


class SmartHeatingAPIClient:
    """Client for IoT Platform Smart Heating API."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        customer_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._customer_id = customer_id
        self._session = session
        self._installation_id: str | None = None

    @property
    def installation_id(self) -> str | None:
        """Return the installation ID."""
        return self._installation_id

    @installation_id.setter
    def installation_id(self, value: str) -> None:
        """Set the installation ID."""
        self._installation_id = value

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an API request."""
        url = f"{self._api_url}{endpoint}"

        # Add customer_id to params
        if params is None:
            params = {}
        params["customer_id"] = self._customer_id

        if self._session is None:
            self._session = aiohttp.ClientSession()

        try:
            async with self._session.request(
                method,
                url,
                json=data,
                params=params,
                headers=self._get_headers(),
            ) as response:
                if response.status == 401:
                    raise SmartHeatingAuthError(
                        "Invalid API key",
                        status_code=401,
                    )
                if response.status == 403:
                    raise SmartHeatingAuthError(
                        "API key lacks required permissions",
                        status_code=403,
                    )

                response.raise_for_status()
                return await response.json()

        except ClientResponseError as err:
            _LOGGER.error("API error: %s %s - %s", method, url, err)
            raise SmartHeatingAPIError(
                f"API error: {err.message}",
                status_code=err.status,
            ) from err
        except ClientError as err:
            _LOGGER.error("Connection error: %s", err)
            raise SmartHeatingConnectionError(
                f"Connection error: {err}",
            ) from err

    async def test_connection(self) -> bool:
        """Test the API connection."""
        try:
            if self._installation_id:
                await self.get_installation()
            else:
                # Just verify the API key works by trying to get installation
                # This will fail if no installation exists, but auth will work
                try:
                    await self.get_installation()
                except SmartHeatingAPIError as err:
                    if err.status_code == 404:
                        # No installation yet, but auth worked
                        return True
                    raise
            return True
        except SmartHeatingAuthError:
            return False
        except SmartHeatingConnectionError:
            return False

    async def register_installation(
        self,
        name: str,
        ha_version: str | None = None,
        ha_url: str | None = None,
        price_area: str = "SE3",
        outdoor_temp_entity_id: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        timezone: str = "Europe/Stockholm",
        default_min_temp: float = 16.0,
        default_max_temp: float = 24.0,
        default_target_temp: float = 20.0,
    ) -> dict[str, Any]:
        """Register a new HA installation."""
        data = {
            "name": name,
            "ha_version": ha_version,
            "ha_url": ha_url,
            "price_area": price_area,
            "outdoor_temp_entity_id": outdoor_temp_entity_id,
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "default_min_temp_c": default_min_temp,
            "default_max_temp_c": default_max_temp,
            "default_target_temp_c": default_target_temp,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        result = await self._request("POST", API_REGISTER, data=data)
        self._installation_id = result.get("installation_id")
        return result

    async def get_installation(self) -> dict[str, Any]:
        """Get the current installation details."""
        return await self._request("GET", API_INSTALLATION)

    async def update_installation(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update installation settings."""
        # Remove None values
        data = {k: v for k, v in kwargs.items() if v is not None}
        return await self._request("PUT", API_INSTALLATION, data=data)

    async def create_zone(
        self,
        name: str,
        temperature_entity_id: str,
        climate_entity_id: str,
        heating_type: str = "unknown",
        humidity_entity_id: str | None = None,
        power_entity_id: str | None = None,
        valve_entity_id: str | None = None,
        supply_temp_entity_id: str | None = None,
        return_temp_entity_id: str | None = None,
        ha_area_id: str | None = None,
        ha_area_name: str | None = None,
        min_temp: float | None = None,
        max_temp: float | None = None,
        target_temp: float | None = None,
        auto_control_enabled: bool = True,
    ) -> dict[str, Any]:
        """Create a new zone."""
        data = {
            "name": name,
            "heating_type": heating_type,
            "temperature_entity_id": temperature_entity_id,
            "climate_entity_id": climate_entity_id,
            "humidity_entity_id": humidity_entity_id,
            "power_entity_id": power_entity_id,
            "valve_entity_id": valve_entity_id,
            "supply_temp_entity_id": supply_temp_entity_id,
            "return_temp_entity_id": return_temp_entity_id,
            "ha_area_id": ha_area_id,
            "ha_area_name": ha_area_name,
            "min_temp_c": min_temp,
            "max_temp_c": max_temp,
            "target_temp_c": target_temp,
            "auto_control_enabled": auto_control_enabled,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        return await self._request("POST", API_ZONES, data=data)

    async def get_zones(self) -> list[dict[str, Any]]:
        """Get all zones for the installation."""
        result = await self._request("GET", API_ZONES)
        return result.get("zones", [])

    async def get_zone(self, zone_id: str) -> dict[str, Any]:
        """Get a specific zone."""
        return await self._request("GET", f"{API_ZONES}/{zone_id}")

    async def update_zone(
        self,
        zone_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update a zone."""
        # Remove None values
        data = {k: v for k, v in kwargs.items() if v is not None}
        return await self._request("PUT", f"{API_ZONES}/{zone_id}", data=data)

    async def delete_zone(self, zone_id: str) -> None:
        """Delete a zone."""
        await self._request("DELETE", f"{API_ZONES}/{zone_id}")

    async def send_telemetry(
        self,
        zones: list[dict[str, Any]],
        outdoor_temp: float | None = None,
        ha_version: str | None = None,
        component_version: str | None = None,
    ) -> dict[str, Any]:
        """Send telemetry data for zones."""
        if not self._installation_id:
            raise SmartHeatingAPIError("Installation ID not set")

        data = {
            "installation_id": self._installation_id,
            "zones": zones,
            "outdoor_temp_c": outdoor_temp,
            "ha_version": ha_version,
            "component_version": component_version,
        }
        # Remove None values at top level
        data = {k: v for k, v in data.items() if v is not None}

        return await self._request("POST", API_TELEMETRY, data=data)

    async def trigger_optimization(
        self,
        force: bool = False,
        target_date: str | None = None,
    ) -> dict[str, Any]:
        """Trigger optimization for the installation."""
        if not self._installation_id:
            raise SmartHeatingAPIError("Installation ID not set")

        data = {
            "installation_id": self._installation_id,
            "force_reoptimize": force,
            "target_date": target_date,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        return await self._request("POST", API_OPTIMIZE, data=data)

    async def get_dashboard(self) -> dict[str, Any]:
        """Get dashboard summary."""
        return await self._request("GET", API_DASHBOARD)

    async def get_pending_setpoints(self) -> dict[str, Any]:
        """Get pending setpoint commands to apply."""
        return await self._request("GET", "/ha-integration/setpoints/pending")

    async def acknowledge_setpoint(
        self,
        command_id: str,
        applied: bool = True,
        actual_temp_c: float | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Acknowledge that a setpoint was applied (or failed)."""
        data = {
            "command_id": command_id,
            "applied": applied,
            "actual_temp_c": actual_temp_c,
            "error_message": error_message,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        return await self._request("POST", "/ha-integration/setpoints/acknowledge", data=data)

    async def get_vacation_mode(self) -> dict[str, Any]:
        """Get current vacation mode settings.

        Returns dict with:
        - enabled: bool
        - start_date: str (YYYY-MM-DD) or None
        - end_date: str (YYYY-MM-DD) or None
        - target_temp_c: float
        - pre_heat_hours: int
        """
        installation = await self.get_installation()
        return {
            "enabled": installation.get("vacation_mode_enabled", False),
            "start_date": installation.get("vacation_start_date"),
            "end_date": installation.get("vacation_end_date"),
            "target_temp_c": installation.get("vacation_target_temp_c", 15.0),
            "pre_heat_hours": installation.get("vacation_pre_heat_hours", 4),
        }

    async def update_vacation_mode(
        self,
        enabled: bool,
        start_date: str | None = None,
        end_date: str | None = None,
        target_temp_c: float = 15.0,
        pre_heat_hours: int = 4,
    ) -> dict[str, Any]:
        """Enable or disable vacation mode.

        Args:
            enabled: Whether vacation mode should be active
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            target_temp_c: Target temperature during vacation (default 15°C)
            pre_heat_hours: Hours before return to start pre-heating (default 4)

        Returns:
            Updated installation data
        """
        data = {
            "enabled": enabled,
            "start_date": start_date,
            "end_date": end_date,
            "target_temp_c": target_temp_c,
            "pre_heat_hours": pre_heat_hours,
        }
        return await self._request("PATCH", API_VACATION, data=data)

    async def set_zone_target_temp(
        self,
        zone_id: str,
        target_temp_c: float,
    ) -> dict[str, Any]:
        """Set target temperature for a zone (bidirectional control).

        This allows HA to set the target temp which will be used by
        the IoT Platform optimizer.

        Args:
            zone_id: UUID of the zone
            target_temp_c: Target temperature in Celsius

        Returns:
            Updated zone data
        """
        data = {"target_temp_c": target_temp_c}
        return await self._request("PUT", f"{API_ZONES}/{zone_id}", data=data)

    async def close(self) -> None:
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None
