"""Domain errors: HTTP-free, mapped to responses by ninja_devx's ErrorMap."""

from ninja_devx.layers import Conflict


class OutOfStock(Conflict):
    code = "out_of_stock"


class AlreadyCancelled(Conflict):
    code = "already_cancelled"
