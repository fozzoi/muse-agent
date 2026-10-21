import asyncio
from core.config import Config
from core.sandbox import run_tool
from core.screen_stream import grab_screen_frame
from google import genai
from google.genai import types

# Tool declarations exposed to the live audio session
LIVE_TOOLS = [
    {
        "name": "desktop_navigator",
        "description": (
            "Click, type, or interact with an element on the user screen."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["click", "double_click", "type", "key"],
                },
                "x": {
                    "type": "INTEGER",
                    "description": "X coordinate on 1920x1080 screen",
                },
                "y": {
                    "type": "INTEGER",
                    "description": "Y coordinate on 1920x1080 screen",
                },
                "text": {
                    "type": "STRING",
                    "description": "Text to type or key name",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "media_control",
        "description": "Adjust volume or toggle playback.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "enum": ["volume_up", "volume_down", "mute", "play_pause"],
                }
            },
            "required": ["action"],
        },
    },
]


async def run_live_session(audio_in_queue, audio_out_queue):
  """Bi-directional live session: audio in/out + periodic screen frames."""
  client = genai.Client(api_key=Config.GEMINI_API_KEY)

  config = types.LiveConnectConfig(
      response_modalities=[types.LiveClientConfigModality.AUDIO],
      system_instruction=types.Content(
          parts=[
              types.Part.from_text(
                  "You are Muse, an autonomous voice AI running on the user's"
                  " Linux machine. You can see their active screen in real-time"
                  " via streamed frames. You can talk freely, see their screen,"
                  " and execute actions using tools when requested. Keep voice"
                  " responses concise and conversational."
              )
          ]
      ),
      tools=[{"function_declarations": LIVE_TOOLS}],
  )

  async with client.aio.live.connect(
      model="gemini-2.5-flash", config=config
  ) as session:
    print("[Live Agent] Connected to Gemini Live!")

    # 1. Background Screen Streamer (1 frame every 1.5 seconds)
    async def send_screen_loop():
      while True:
        try:
          frame_bytes = grab_screen_frame()
          await session.send(
              input={"mime_type": "image/jpeg", "data": frame_bytes}
          )
          await asyncio.sleep(1.5)
        except Exception as e:
          print(f"[Screen Stream Error] {e}")
          break

    # 2. Audio Input Streamer (Mic to Gemini)
    async def send_audio_loop():
      while True:
        pcm_chunk = await audio_in_queue.get()
        await session.send(
            input={"mime_type": "audio/pcm;rate=16000", "data": pcm_chunk}
        )

    # 3. Receiver Loop (Gemini Audio & Tool Execution to You)
    async def receive_loop():
      async for response in session.receive():
        # Handle audio output back to speakers
        server_content = response.server_content
        if server_content and server_content.model_turn:
          for part in server_content.model_turn.parts:
            if part.inline_data:
              await audio_out_queue.put(part.inline_data.data)

        # Handle tool call while talking
        tool_call = response.tool_call
        if tool_call:
          for call in tool_call.function_calls:
            print(f"[Live Agent] Calling tool: {call.name} with {call.args}")
            success, output = run_tool(call.name, dict(call.args))

            # Send tool output back to live session
            await session.send(
                input=types.LiveClientToolResponse(
                    function_responses=[
                        types.FunctionResponse(
                            name=call.name, id=call.id, response={"output": output}
                        )
                    ]
                )
            )

    await asyncio.gather(send_screen_loop(), send_audio_loop(), receive_loop())