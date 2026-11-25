"""Cammy desktop companion orchestration (offline slice; see docs/COMPANION-SERVICE.md).

Importing this package performs no I/O. Body access goes only through a body
adapter; providers are injected. SPDX-License-Identifier: MIT
"""
from .body import BodyAdapter, BodyError, SdkBody, SimulatorBody, describe_capabilities
from .contracts import (AgentReply, AgentRequest, AudioClip, CANCELLED, LIVE_UNVERIFIED, Outcome,
                        SpeechClip, Submission, SYNTHETIC, Transcript, TYPED)
from .service import (CompanionConfig, CompanionService, HALTED, IDLE, LISTENING, PAUSED, RUNNING,
                      SPEAKING, STOPPED, THINKING, split_speech)
from .engagement import EngagementConfig, EngagementController

__all__ = ['AgentReply', 'AgentRequest', 'AudioClip', 'BodyAdapter', 'BodyError', 'CANCELLED',
           'CompanionConfig', 'CompanionService', 'EngagementConfig', 'EngagementController',
           'HALTED', 'IDLE', 'LISTENING', 'LIVE_UNVERIFIED', 'Outcome',
           'PAUSED', 'RUNNING', 'SPEAKING', 'STOPPED', 'SYNTHETIC', 'SdkBody', 'SimulatorBody', 'SpeechClip',
           'Submission', 'THINKING', 'TYPED', 'Transcript', 'describe_capabilities', 'split_speech']
