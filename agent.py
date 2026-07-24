import os
import certifi

# Fix for macOS SSL Certificate errors - MUST be before other imports
os.environ['SSL_CERT_FILE'] = certifi.where()

import logging
import json
from dotenv import load_dotenv

from livekit import agents, api
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import (
    openai,
    cartesia,
    deepgram,
    noise_cancellation,
    silero,
    sarvam,
)
from livekit.agents import llm
from typing import Annotated, Optional

# Load environment variables
load_dotenv(".env")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outbound-agent")

import config

# TRUNK ID - Now loaded from config.py
# You can find this by running 'python setup_trunk.py --list' or checking LiveKit Dashboard 


def _make_sarvam_tts(language_code: str = None, voice: str = None):
    """Build a Sarvam TTS tuned for natural, human-sounding Indian speech.

    Single source of truth so the initial voice and every mid-call language
    switch sound identical.
    """
    return sarvam.TTS(
        model=os.getenv("SARVAM_TTS_MODEL", config.SARVAM_MODEL),
        speaker=voice or os.getenv("SARVAM_VOICE", "pooja"),
        target_language_code=language_code or os.getenv("SARVAM_LANGUAGE", config.SARVAM_LANGUAGE),
        # Naturalness tuning (bulbul:v3):
        # - temperature: higher = more prosody variation, less monotone (default 0.6)
        # - pace: 1.0 is natural conversational speed
        # - enable_preprocessing: speaks numbers, currency and mixed-language text naturally
        # - larger buffer/chunks: the engine shapes intonation over whole sentences,
        #   which sounds smoother and more human than clip-by-clip synthesis
        temperature=float(os.getenv("SARVAM_TTS_TEMPERATURE", str(config.SARVAM_TTS_TEMPERATURE))),
        pace=float(os.getenv("SARVAM_TTS_PACE", str(config.SARVAM_TTS_PACE))),
        enable_preprocessing=True,
        # Low buffer = start speaking after ~40 chars instead of waiting for 80,
        # so the first words come out fast (less dead air after the caller speaks).
        min_buffer_size=40,
        max_chunk_length=180,
    )


def _build_tts(config_provider: str = None, config_voice: str = None):
    """Configure the Text-to-Speech provider based on env vars or dynamic config."""
    # Priority: Config > Env Var > Default
    provider = (config_provider or os.getenv("TTS_PROVIDER", config.DEFAULT_TTS_PROVIDER)).lower()

    # If using a Sarvam voice name, force Sarvam provider
    if config_voice in ["anushka", "aravind", "amartya", "dhruv", "pooja", "rahul"]:
        provider = "sarvam"

    if provider == "cartesia":
        logger.info("Using Cartesia TTS")
        model = os.getenv("CARTESIA_TTS_MODEL", config.CARTESIA_MODEL)
        voice = os.getenv("CARTESIA_TTS_VOICE", config.CARTESIA_VOICE)
        return cartesia.TTS(model=model, voice=voice)
    
    if provider == "sarvam":
        logger.info(f"Using Sarvam TTS (Voice: {config_voice})")
        return _make_sarvam_tts(voice=config_voice)

    if provider == "deepgram":
        logger.info("Using Deepgram TTS")
        model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-asteria-en")
        return deepgram.TTS(model=model)

    # Default to OpenAI
    logger.info(f"Using OpenAI TTS (Voice: {config_voice})")
    model = os.getenv("OPENAI_TTS_MODEL", "tts-1")
    voice = config_voice or os.getenv("OPENAI_TTS_VOICE", config.DEFAULT_TTS_VOICE)
    return openai.TTS(model=model, voice=voice)


def _make_sarvam_stt(language: str = None):
    """Build a Sarvam STT. Explicit language transcribes short words ('yes', 'haan')
    far more reliably than 'unknown' auto-detect, so we start in en-IN and hot-swap
    to the caller's chosen language via set_language()."""
    model = os.getenv("SARVAM_STT_MODEL", config.SARVAM_STT_MODEL)
    language = language or os.getenv("SARVAM_STT_LANGUAGE", config.SARVAM_STT_LANGUAGE)
    logger.info(f"Using Sarvam STT (Language: {language})")
    return sarvam.STT(model=model, language=language)


def _build_stt(config_provider: str = None):
    """Configure the Speech-to-Text provider based on env vars or dynamic config."""
    provider = (config_provider or os.getenv("STT_PROVIDER", config.STT_PROVIDER)).lower()

    if provider == "sarvam":
        return _make_sarvam_stt()

    logger.info("Using Deepgram STT")
    return deepgram.STT(model=config.STT_MODEL, language=config.STT_LANGUAGE)


def _build_llm(config_provider: str = None):
    """Configure the LLM provider based on config or env vars."""
    provider = (config_provider or os.getenv("LLM_PROVIDER", config.DEFAULT_LLM_PROVIDER)).lower()

    if provider == "groq":
        logger.info("Using Groq LLM")
        return openai.LLM(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY"),
            model=os.getenv("GROQ_MODEL", config.GROQ_MODEL),
            temperature=float(os.getenv("GROQ_TEMPERATURE", str(config.GROQ_TEMPERATURE))),
        )
    
    # Default to OpenAI
    logger.info("Using OpenAI LLM")
    return openai.LLM(model=config.DEFAULT_LLM_MODEL)



class TransferFunctions(llm.ToolContext):
    def __init__(self, ctx: agents.JobContext, phone_number: str = None):
        super().__init__(tools=[])
        self.ctx = ctx
        self.phone_number = phone_number
        self.agent: Agent | None = None  # Set once the Agent/session are created in entrypoint()

    @llm.function_tool(
        description="Call this exactly once, right after the caller states which language "
        "they want to continue in. Switches the voice and transcription to that language."
    )
    async def set_language(self, language: str):
        """
        Args:
            language: The language the caller chose - one of "english", "hindi", "kannada".
        """
        codes = {"english": "en-IN", "hindi": "hi-IN", "kannada": "kn-IN"}
        lang_code = codes.get(language.strip().lower())
        if not lang_code:
            return "Unrecognized language. Please ask the caller to choose English, Hindi, or Kannada."

        if self.agent is not None:
            # Swap BOTH directions: the voice speaks the new language and the
            # transcriber listens in it (explicit language = better accuracy).
            self.agent.update_options(
                tts=_make_sarvam_tts(language_code=lang_code),
                stt=_make_sarvam_stt(language=lang_code),
            )
            logger.info(f"Switched TTS+STT language to {language} ({lang_code})")
        else:
            logger.warning("set_language called before agent was ready; ignoring")

        return f"Language switched to {language}."

    @llm.function_tool(description="Look up user details by phone number.")
    async def lookup_user(self, phone: str):
        """
        Mock function to look up user details.

        Args:
            phone: The phone number to look up
        """
        logger.info(f"Looking up user: {phone}")
        return f"User found: Shreyas Raj. Status: Premium. Last order: Coffee setup (Delivered)."

    @llm.function_tool(description="Transfer the call to a human support agent or another phone number.")
    async def transfer_call(self, destination: Optional[str] = None):
        """
        Transfer the call.
        """
        if destination is None:
            destination = config.DEFAULT_TRANSFER_NUMBER
            if not destination:
                 return "Error: No default transfer number configured."
        if "@" not in destination:
            # If no domain is provided, append the SIP domain
            if config.SIP_DOMAIN:
                # Ensure clean number (strip tel: or sip: prefix if present but no domain)
                clean_dest = destination.replace("tel:", "").replace("sip:", "")
                destination = f"sip:{clean_dest}@{config.SIP_DOMAIN}"
            else:
                # Fallback to tel URI if no domain configured
                if not destination.startswith("tel:") and not destination.startswith("sip:"):
                     destination = f"tel:{destination}"
        elif not destination.startswith("sip:"):
             destination = f"sip:{destination}"
        
        logger.info(f"Transferring call to {destination}")
        
        # Determine the participant identity
        # For outbound calls initiated by this agent, the participant identity is typically "sip_<phone_number>"
        # For inbound, we might need to find the remote participant.
        participant_identity = None
        
        # If we stored the phone number from metadata, we can construct the identity
        if self.phone_number:
            participant_identity = f"sip_{self.phone_number}"
        else:
            # Try to find a participant that is NOT the agent
            for p in self.ctx.room.remote_participants.values():
                participant_identity = p.identity
                break
        
        if not participant_identity:
            logger.error("Could not determine participant identity for transfer")
            return "Failed to transfer: could not identify the caller."

        try:
            logger.info(f"Transferring participant {participant_identity} to {destination}")
            await self.ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=self.ctx.room.name,
                    participant_identity=participant_identity,
                    transfer_to=destination,
                    play_dialtone=False
                )
            )
            return "Transfer initiated successfully."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return f"Error executing transfer: {e}"


class OutboundAssistant(Agent):
    """
    An AI agent tailored for outbound calls.
    Attempts to be helpful and concise.
    """
    def __init__(self, tools: list, instructions: str = None) -> None:
        super().__init__(
            instructions=instructions or config.SYSTEM_PROMPT,
            tools=tools,
        )




async def entrypoint(ctx: agents.JobContext):
    """
    Main entrypoint for the agent.
    
    For outbound calls:
    1. Checks for 'phone_number' in the job metadata.
    2. Connects to the room.
    3. Initiates the SIP call to the phone number.
    4. Waits for answer before speaking.
    """
    logger.info(f"Connecting to room: {ctx.room.name}")
    
    # parse the phone number AND config from the metadata
    phone_number = None
    config_dict = {}
    
    # Check Job Metadata (Legacy/Dispatch)
    try:
        if ctx.job.metadata:
            data = json.loads(ctx.job.metadata)
            phone_number = data.get("phone_number")
            config_dict = data
    except Exception:
        pass
        
    # Check Room Metadata (Dashboard/Route.ts) - Overrides Job Metadata if present
    try:
        if ctx.room.metadata:
            data = json.loads(ctx.room.metadata)
            if data.get("phone_number"):
                phone_number = data.get("phone_number")
            config_dict.update(data) # Merge configs
    except Exception:
        logger.warning("No valid JSON metadata found in Room.")

    # Initialize function context
    fnc_ctx = TransferFunctions(ctx, phone_number)

    # Initialize the Agent Session with plugins
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=_build_stt(config_dict.get("stt_provider")),
        llm=_build_llm(config_dict.get("model_provider")),
        tts=_build_tts(config_dict.get("tts_provider"), config_dict.get("voice_id")),
        # Snappy, human-like turn-taking:
        # - endpointing min_delay 0.2s: reply almost immediately after the caller stops (default 0.5)
        # - preemptive generation + preemptive TTS: prepare the reply (and its audio)
        #   WHILE the caller is finishing, so speech starts near-instantly on their stop
        turn_handling={
            "endpointing": {"min_delay": 0.2},
            "preemptive_generation": {"enabled": True, "preemptive_tts": True},
        },
    )

    # Start the session
    # Compose the system prompt from the engine + the target sector's playbook (if provided
    # via metadata) + any free-text context typed into the dashboard.
    instructions = config.build_instructions(
        sector=config_dict.get("sector"),
        extra_context=config_dict.get("user_prompt"),
    )
    agent = OutboundAssistant(
        tools=list(fnc_ctx.function_tools.values()),
        instructions=instructions,
    )
    fnc_ctx.agent = agent  # lets set_language() hot-swap this agent's TTS mid-call
    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
            close_on_disconnect=True, # Close room when agent disconnects
        ),
    )

    # Silence watchdog: if the caller says nothing for ~15s (or their short reply
    # was missed by STT), don't sit dead - check in and keep the call moving.
    @session.on("user_state_changed")
    def _on_user_state_changed(ev):
        if ev.new_state == "away":
            logger.info("Caller quiet for a while - agent checking in")
            session.generate_reply(
                instructions=(
                    "The caller has been quiet, or their short reply may not have been heard. "
                    "Gently check in in one short sentence - e.g. 'Hello, are you there?' - and "
                    "if you had just asked them something, repeat it briefly in a fresh way. "
                    "If they had just agreed to talk, continue to your pitch."
                )
            )

    # Logic to dial out:
    # 1. If 'phone_number' is present, we MIGHT need to dial.
    # 2. Check if a SIP participant is already in the room (Dashboard dispatch case).
    
    should_dial = False
    if phone_number:
        # Check if any remote participant looks like our user (sip_PHONE)
        user_already_here = False
        for p in ctx.room.remote_participants.values():
            if f"sip_{phone_number}" in p.identity or "sip_" in p.identity:
                user_already_here = True
                break
        
        if not user_already_here:
            should_dial = True
            logger.info("User not in room. Agent will initiate dial-out.")
        else:
            logger.info("User already in room (Dashboard dispatched). output Only generated greeting.")

    if should_dial:
        logger.info(f"Initiating outbound SIP call to {phone_number}...")
        try:
            # Create a SIP participant to dial out
            # This effectively "calls" the phone number and brings them into this room
            # --- CONNECTING TO THE PHONE NETWORK ---
            # This step actually "dials" the number using Vobiz (SIP Trunk).
            # It invites the phone number into this digital room.
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=config.SIP_TRUNK_ID,
                    sip_call_to=phone_number,
                    participant_identity=f"sip_{phone_number}", # Unique ID for the SIP user
                    wait_until_answered=True, # Important: Wait for pickup before continuing
                )
            )
            logger.info("Call answered! Agent is now listening.")
            
            # Note: We do NOT generate an initial reply here immediately.
            # Usually for outbound, we want to hear "Hello?" from the user first,
            # OR we can speak immediately. 
            # If you want the agent to speak first, uncomment the lines below:
            
            await session.generate_reply(
                instructions=config.INITIAL_GREETING
            )
            
        except Exception as e:
            logger.error(f"Failed to place outbound call: {e}")
            # Ensure we clean up if the call fails
            ctx.shutdown()
    else:
        # Fallback for inbound calls (if this agent is used for that) OR Dashboard calls where user is already there
        logger.info("Detecting if we should greet...")
        # Give a small delay for audio to stabilize if user just joined
        await session.generate_reply(instructions=config.fallback_greeting)


def prewarm(proc: agents.JobProcess):
    # Load the VAD model once per worker process instead of on every call.
    proc.userdata["vad"] = silero.VAD.load()


if __name__ == "__main__":
    # The agent name "outbound-caller" is used by the dispatch script to find this worker
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="outbound-caller",
        )
    )
