"""The API service (roadmap 2C): a multi-tenant HTTP front for the judge.

Every conversation is de-identified before it is stored or sent to a model, every query is
scoped by clinic_id, and the judge fails closed (a failure is needs_review, never pass).
"""
