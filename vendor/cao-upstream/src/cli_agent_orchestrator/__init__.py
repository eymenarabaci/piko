"""CLI Agent Orchestrator, with the AI-Orchestrator Windows port enabled.

Platform-specific capabilities are selected by the configured terminal backend.
Importing the package must stay side-effect free; native Windows support is
provided by the ConPTY backend rather than a package-wide platform rejection.
"""
