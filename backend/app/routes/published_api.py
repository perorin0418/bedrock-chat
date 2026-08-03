import json
import logging
import os
from time import sleep

import boto3
from app.repositories.api_key_owner import find_api_key_owner
from app.routes.schemas.conversation import ChatInput, Conversation, MessageInput
from app.routes.schemas.published_api import (
    ChatInputWithoutBotId,
    ChatOutputWithoutBotId,
    MessageRequestedResponse,
)
from app.usecases.chat import chat, fetch_conversation
from app.usecases.rate_limit import check_rate_limit
from app.user import User
from fastapi import APIRouter, HTTPException, Request
from ulid import ULID

logger = logging.getLogger(__name__)

router = APIRouter(tags=["published_api"])

sqs_client = boto3.client("sqs")
QUEUE_URL = os.environ.get("QUEUE_URL", "")


def _get_request_api_key_id(request: Request) -> str | None:
    """Extract the API Gateway API key id used for this request.

    Populated by the Lambda Web Adapter, which forwards the original Lambda
    event's `requestContext` as this header. Returns None if the header is
    absent or unparseable (e.g. local development), in which case the caller
    should skip rate-limit attribution rather than fail the request.
    """
    raw_context = request.headers.get("x-amzn-request-context")
    if not raw_context:
        return None
    try:
        return json.loads(raw_context).get("identity", {}).get("apiKeyId")
    except (json.JSONDecodeError, AttributeError):
        return None


@router.get("/health")
def health():
    """For health check"""
    return {"status": "ok"}


@router.post("/conversation", response_model=MessageRequestedResponse)
def post_message(request: Request, message_input: ChatInputWithoutBotId):
    """Send chat message"""
    current_user: User = request.state.current_user

    # Extract bot_id from `current_user.id`
    # NOTE: user_id naming rule is implemented on `add_current_user_to_request` method
    bot_id = (
        current_user.id.split("#")[1] if "#" in current_user.id else current_user.id
    )

    api_key_id = _get_request_api_key_id(request)
    rate_limit_user_id = find_api_key_owner(api_key_id) if api_key_id else None
    if rate_limit_user_id is not None:
        check_rate_limit(rate_limit_user_id)
    else:
        logger.warning(
            f"Published API key {api_key_id} has no bound owner; skipping "
            "rate-limit check for this request."
        )

    # Generate conversation id if not provided
    conversation_id = (
        str(ULID())
        if message_input.conversation_id is None
        else message_input.conversation_id
    )
    # Issue id for the response message
    response_message_id = str(ULID())

    chat_input = ChatInput(
        conversation_id=conversation_id,
        message=MessageInput(
            role="user",
            content=message_input.message.content,
            model=message_input.message.model,
            parent_message_id=None,  # Use the latest message as the parent
            message_id=response_message_id,
        ),
        bot_id=bot_id,
        continue_generate=message_input.continue_generate,
        enable_reasoning=message_input.enable_reasoning,
        rate_limit_user_id=rate_limit_user_id,
    )

    try:
        _ = sqs_client.send_message(
            QueueUrl=QUEUE_URL, MessageBody=chat_input.model_dump_json()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MessageRequestedResponse(
        conversation_id=conversation_id, message_id=response_message_id
    )


@router.get("/conversation/{conversation_id}", response_model=Conversation)
def get_conversation(request: Request, conversation_id: str):
    """Get a conversation history. If the conversation does not exist, it will return 404."""
    current_user: User = request.state.current_user

    output = fetch_conversation(current_user.id, conversation_id)
    return output


@router.get(
    "/conversation/{conversation_id}/{message_id}",
    response_model=ChatOutputWithoutBotId,
)
def get_message(request: Request, conversation_id: str, message_id: str):
    """Get specified message in a conversation. If the message does not exist, it will return 404."""
    current_user: User = request.state.current_user

    conversation = fetch_conversation(current_user.id, conversation_id)
    input_message = conversation.message_map.get(message_id, None)
    if input_message is None:
        raise HTTPException(
            status_code=404,
            detail=f"Message {message_id} not found in conversation {conversation_id}",
        )
    output_message_id = input_message.children[0]
    output_message = conversation.message_map.get(output_message_id, None)
    if output_message is None:
        raise HTTPException(
            status_code=404,
            detail=f"Message {message_id} not found in conversation {conversation_id}",
        )

    return ChatOutputWithoutBotId(
        conversation_id=conversation_id,
        message=output_message,
        create_time=conversation.create_time,
    )
