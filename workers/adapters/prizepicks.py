"""prizepicks adapter — domain specifics only. Keep it thin.

Implements exactly four things against core.agent.BaseAgent:
    observe()             fetch domain data
    form_thesis(obs)      reason about it
    build_commitment(t)   produce the payload + legs
    resolve(commitment)   determine what actually happened

Everything else is core/'s job.
"""
