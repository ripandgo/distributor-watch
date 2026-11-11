"""Rip and Go distributor watch: GitHub-Actions change detector.

Polls public distributor sources, the shop Gmail (read-only IMAP) and the
ASM inventory PDF, filters noise, and POSTs a JSON payload to the Grok Bot
routine webhook only when something real changed. Quiet runs exit 0 silently.
"""
