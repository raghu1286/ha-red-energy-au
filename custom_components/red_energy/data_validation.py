"""Data validation utilities for Red Energy integration."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class DataValidationError(Exception):
    """Exception raised when data validation fails."""


def validate_customer_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate customer data from Red Energy API."""
    if not isinstance(data, dict):
        raise DataValidationError("Customer data must be a dictionary")
    
    # Try different field mappings that Red Energy API might use
    customer_id = (
        data.get("id") or 
        data.get("customer_id") or 
        data.get("customerId") or 
        data.get("accountId") or
        "unknown"
    )
    
    customer_name = (
        data.get("name") or
        data.get("customer_name") or
        data.get("customerName") or
        data.get("full_name") or
        data.get("displayName") or
        "Red Energy Customer"
    )
    
    customer_email = (
        data.get("email") or
        data.get("email_address") or
        data.get("emailAddress") or
        "unknown@redenergy.com.au"
    )
    
    # Sanitize data
    validated_data = {
        "id": str(customer_id),
        "name": str(customer_name).strip(),
        "email": str(customer_email).strip().lower(),
    }
    
    # Add optional fields if present
    phone = data.get("phone") or data.get("phone_number") or data.get("phoneNumber")
    if phone:
        validated_data["phone"] = str(phone).strip()
    
    return validated_data


def validate_properties_data(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate properties data from Red Energy API."""
    if not isinstance(data, list):
        raise DataValidationError("Properties data must be a list")
    
    if not data:
        raise DataValidationError("No properties found in response")
    
    validated_properties = []
    
    for i, property_data in enumerate(data):
        try:
            validated_property = validate_single_property(property_data)
            validated_properties.append(validated_property)
        except DataValidationError as err:
            _LOGGER.error("Property %d validation failed: %s", i, err)
            # Skip invalid properties rather than failing completely
            continue
    
    if not validated_properties:
        raise DataValidationError("No valid properties after validation")
    
    return validated_properties


def validate_single_property(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a single property."""
    if not isinstance(data, dict):
        raise DataValidationError("Property data must be a dictionary")
    
    # Try different field mappings for property ID
    property_id = (
        data.get("id") or 
        data.get("property_id") or 
        data.get("propertyId") or 
        data.get("propertyNumber") or  # Red Energy uses this as primary ID
        data.get("propertyPhysicalNumber") or  # Red Energy secondary ID
        data.get("account_id") or
        data.get("accountId") or
        data.get("accountNumber") or  # Red Energy account number
        data.get("number") or
        data.get("premise_id") or
        str(hash(str(data)))[-8:]  # Fallback: generate from data hash
    )
    
    if not property_id:
        raise DataValidationError("Unable to determine property identifier from available fields")
    
    # Try different field mappings for property name
    property_name = (
        data.get("name") or
        data.get("property_name") or
        data.get("propertyName") or
        data.get("display_name") or
        data.get("address_name") or
        # Red Energy: try to build name from address
        (data.get("address", {}).get("displayAddress") or "").replace('\n', ', ') or
        (data.get("address", {}).get("gentrackDisplayAddress") or "").replace('\n', ', ') or
        f"Property {property_id}"
    )
    
    validated_property = {
        "id": str(property_id),
        "name": str(property_name).strip(),
        "address": validate_address(data.get("address", {}) or data.get("location", {})),
        "services": validate_services(
            data.get("services", []) or 
            data.get("meters", []) or 
            data.get("consumers", [])  # Red Energy uses "consumers" array
        ),
    }
    
    return validated_property


def validate_address(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate address data."""
    if not isinstance(data, dict):
        data = {}
    
    # Try different field mappings for address components
    street = (
        data.get("street") or
        data.get("street_address") or
        data.get("streetAddress") or
        data.get("address_line_1") or
        data.get("line1") or
        ""
    ).strip()
    
    # Red Energy uses "house" + "street" format, combine them if both present
    if data.get("house") and data.get("street"):
        house = str(data.get("house", "")).strip()
        street_name = str(data.get("street", "")).strip()
        if house and street_name:
            street = f"{house} {street_name}"
    
    city = (
        data.get("city") or
        data.get("suburb") or  # Red Energy uses "suburb" field
        data.get("town") or
        data.get("locality") or
        ""
    ).strip()
    
    state = (
        data.get("state") or
        data.get("province") or
        data.get("region") or
        ""
    ).strip()
    
    postcode = (
        data.get("postcode") or
        data.get("postal_code") or
        data.get("zip") or
        data.get("zip_code") or
        ""
    ).strip()
    
    return {
        "street": street,
        "city": city,
        "state": state,
        "postcode": postcode,
    }


def validate_services(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate services data."""
    if not isinstance(data, list):
        _LOGGER.debug("Services data is not a list: %s", type(data))
        return []
    
    _LOGGER.debug("Validating %d services: %s", len(data), data)
    validated_services = []
    
    for service in data:
        try:
            validated_service = validate_single_service(service)
            validated_services.append(validated_service)
        except DataValidationError as err:
            _LOGGER.warning("Service validation failed for service %s: %s", service, err)
            continue
    
    return validated_services


def validate_single_service(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a single service."""
    if not isinstance(data, dict):
        raise DataValidationError("Service data must be a dictionary")
    
    _LOGGER.debug("Validating service with fields: %s", list(data.keys()))
    
    # Try different field mappings for service type
    service_type = (
        data.get("type") or
        data.get("service_type") or
        data.get("serviceType") or
        data.get("fuel_type") or
        data.get("fuelType") or
        data.get("energy_type") or
        data.get("utility") or  # Red Energy uses "utility" field
        ""
    ).lower()
    
    # Map different service type names to standard names
    if "elec" in service_type or service_type == "e":
        service_type = "electricity"
    elif "gas" in service_type or service_type == "g":
        service_type = "gas"
    
    if service_type not in ["electricity", "gas"]:
        _LOGGER.warning("Unknown service type '%s', defaulting to electricity", service_type)
        service_type = "electricity"
    
    # Try different field mappings for consumer number
    consumer_number = (
        data.get("consumer_number") or
        data.get("consumerNumber") or  # Red Energy uses this field
        data.get("meter_number") or
        data.get("meterNumber") or
        data.get("account_number") or
        data.get("accountNumber") or
        data.get("nmi") or
        data.get("mirn") or
        data.get("identifier") or
        data.get("id")
    )
    
    if not consumer_number:
        _LOGGER.warning("Service missing consumer number, using fallback")
        consumer_number = f"unknown_{service_type}_{hash(str(data)) % 10000}"
    
    # Try different field mappings for active status
    active = data.get("active", True)
    if "status" in data:
        status = str(data["status"]).lower()
        active = status in ["active", "enabled", "on", "true", "1"]  # Red Energy uses "ON"
    elif "enabled" in data:
        active = bool(data["enabled"])
    
    validated_service = {
        "type": service_type,
        "consumer_number": str(consumer_number),
        "active": bool(active),
    }
    
    _LOGGER.debug("Validated service: %s", validated_service)
    return validated_service


def validate_usage_data(data: Any, consumer_number: str = None, from_date: str = None, to_date: str = None) -> Dict[str, Any]:
    """Validate usage data from Red Energy API.
    
    Supports both formats:
    1. Dictionary format with consumer_number and usage_data fields (legacy)
    2. Array format with daily usage objects (Red Energy API actual format)
    """
    
    # Handle Red Energy API array format
    if isinstance(data, list):
        return _validate_red_energy_usage_array(data, consumer_number, from_date, to_date)
    
    # Handle legacy dictionary format
    elif isinstance(data, dict):
        return _validate_legacy_usage_dict(data)
    
    else:
        raise DataValidationError(f"Usage data must be a dictionary or list, got {type(data)}")


def _validate_red_energy_usage_array(data: List[Dict[str, Any]], consumer_number: str = None, from_date: str = None, to_date: str = None) -> Dict[str, Any]:
    """Validate Red Energy API array format usage data."""
    if not isinstance(data, list):
        raise DataValidationError("Red Energy usage data must be a list")
    
    validated_entries = []
    total_usage = 0.0
    total_cost = 0.0
    
    for daily_data in data:
        if not isinstance(daily_data, dict):
            _LOGGER.warning("Skipping invalid daily usage entry: %s", daily_data)
            continue
            
        usage_date = daily_data.get("usageDate")
        if not usage_date:
            _LOGGER.warning("Daily usage entry missing usageDate: %s", daily_data)
            continue
            
        half_hours = daily_data.get("halfHours", [])
        if not isinstance(half_hours, list):
            _LOGGER.warning("Invalid halfHours data for date %s: %s", usage_date, half_hours)
            continue
        
        # Aggregate daily totals from half-hour intervals
        daily_consumption = 0.0
        daily_cost = 0.0
        daily_generation = 0.0
        
        for interval in half_hours:
            if not isinstance(interval, dict):
                continue
                
            consumption_kwh = float(interval.get("consumptionKwh", 0))
            consumption_dollar_inc_gst = float(interval.get("consumptionDollarIncGst", 0))
            generation_kwh = float(interval.get("generationKwh", 0))
            
            daily_consumption += consumption_kwh
            daily_cost += consumption_dollar_inc_gst
            daily_generation += generation_kwh
        
        # Create validated daily entry
        validated_entry = {
            "date": usage_date,
            "usage": round(daily_consumption, 3),
            "cost": round(daily_cost, 2),
            "generation": round(daily_generation, 3),
            "unit": "kWh",
            "intervals": len(half_hours)
        }
        
        validated_entries.append(validated_entry)
        total_usage += validated_entry["usage"]
        total_cost += validated_entry["cost"]
    
    return {
        "consumer_number": str(consumer_number) if consumer_number else "unknown",
        "from_date": from_date or "",
        "to_date": to_date or "",
        "usage_data": validated_entries,
        "total_usage": round(total_usage, 2),
        "total_cost": round(total_cost, 2),
    }


def _validate_legacy_usage_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate legacy dictionary format usage data."""
    # Required fields
    required_fields = ["consumer_number", "usage_data"]
    for field in required_fields:
        if field not in data:
            raise DataValidationError(f"Usage data missing required field: {field}")
    
    usage_entries = data["usage_data"]
    if not isinstance(usage_entries, list):
        raise DataValidationError("usage_data must be a list")
    
    validated_entries = []
    total_usage = 0.0
    total_cost = 0.0
    
    for entry in usage_entries:
        try:
            validated_entry = validate_usage_entry(entry)
            validated_entries.append(validated_entry)
            total_usage += validated_entry["usage"]
            total_cost += validated_entry["cost"]
        except DataValidationError as err:
            _LOGGER.warning("Usage entry validation failed: %s", err)
            continue
    
    return {
        "consumer_number": str(data["consumer_number"]),
        "from_date": data.get("from_date", ""),
        "to_date": data.get("to_date", ""),
        "usage_data": validated_entries,
        "total_usage": round(total_usage, 2),
        "total_cost": round(total_cost, 2),
    }


def validate_usage_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a single usage data entry."""
    if not isinstance(data, dict):
        raise DataValidationError("Usage entry must be a dictionary")
    
    # Validate date
    date_str = data.get("date")
    if not date_str:
        raise DataValidationError("Usage entry missing date")
    
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as err:
        raise DataValidationError(f"Invalid date format: {date_str}") from err
    
    # Validate usage (must be numeric)
    usage = data.get("usage", 0)
    try:
        usage = float(usage)
        if usage < 0:
            _LOGGER.warning("Negative usage value: %s, setting to 0", usage)
            usage = 0.0
    except (ValueError, TypeError) as err:
        raise DataValidationError(f"Invalid usage value: {usage}") from err
    
    # Validate cost (must be numeric)
    cost = data.get("cost", 0)
    try:
        cost = float(cost)
        if cost < 0:
            _LOGGER.warning("Negative cost value: %s, setting to 0", cost)
            cost = 0.0
    except (ValueError, TypeError) as err:
        raise DataValidationError(f"Invalid cost value: {cost}") from err
    
    return {
        "date": date_str,
        "usage": round(usage, 3),
        "cost": round(cost, 2),
        "unit": data.get("unit", "kWh"),
    }


def sanitize_sensor_name(name: str) -> str:
    """Sanitize a name for use as a sensor name."""
    if not name:
        return "Unknown"
    
    # Remove/replace problematic characters
    sanitized = name.replace("/", "_").replace("\\", "_")
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in " -_.")
    
    # Limit length
    if len(sanitized) > 50:
        sanitized = sanitized[:47] + "..."
    
    return sanitized.strip()


def validate_config_data(config: Dict[str, Any]) -> None:
    """Validate configuration data."""
    required_fields = ["username", "password", "client_id"]
    
    for field in required_fields:
        if field not in config:
            raise DataValidationError(f"Configuration missing required field: {field}")
        
        value = config[field]
        if not value or not isinstance(value, str) or not value.strip():
            raise DataValidationError(f"Configuration field '{field}' must be a non-empty string")
    
    # Validate email format for username
    username = config["username"]
    if "@" not in username or "." not in username.split("@")[-1]:
        raise DataValidationError("Username must be a valid email address")
    
    # Validate client_id format (should be non-empty string)
    client_id = config["client_id"]
    if len(client_id) < 5:
        raise DataValidationError("Client ID appears too short - verify it was captured correctly")
    
    _LOGGER.debug("Configuration validation passed")