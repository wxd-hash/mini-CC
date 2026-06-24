from .permission import PermissionChecker, Mode

# Legacy alias for backward compatibility
PermissionManager = PermissionChecker

__all__ = ["PermissionChecker", "PermissionManager", "Mode"]
