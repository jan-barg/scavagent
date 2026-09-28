import os

# Importing app opens a store; tests use memory unless a test supplies its own.
os.environ.setdefault("SCAVAGENT_STORE", "memory")
