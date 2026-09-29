import logging

# Failure-path tests deliberately make core log tracebacks at ERROR. They are
# the behaviour under test, not noise to read on a green run.
logging.disable(logging.CRITICAL)
