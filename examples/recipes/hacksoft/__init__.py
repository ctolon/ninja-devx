"""HackSoft style: plain service functions for writes, selectors for reads.

Controllers stay thin: validate input (Ninja), call a service or selector, return.
No container is needed; business errors are ``DomainError`` subclasses.
"""
