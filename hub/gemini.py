"""Single-turn Gemini extraction over HTTPS. No local tools or agent runtime."""
from contextlib import closing
import http.client
import json
import re

from jsonschema import Draft202012Validator

from storage import Store, StorageError, decode

DEFAULT_MODEL = 'gemini-3.5-flash-lite'
HOST = 'generativelanguage.googleapis.com'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def model_id(model):
    if not isinstance(model, str) or not re.fullmatch(r'gemini-[a-zA-Z0-9._-]{1,100}', model):
        raise ValueError('Expected a Gemini model ID')
    return model


def structured_result(response, schema):
    if not isinstance(response, dict) or 'error' in response:
        raise ValueError('Invalid Gemini response')
    feedback = response.get('promptFeedback', {})
    if not isinstance(feedback, dict) or feedback.get('blockReason'):
        raise ValueError('Gemini blocked the prompt')
    candidates = response.get('candidates')
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError('Gemini did not return a single candidate')
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get('finishReason') != 'STOP':
        raise ValueError('Gemini did not complete the response')
    if any(key in candidate for key in ('groundingAttributions', 'groundingMetadata', 'urlContextMetadata')):
        raise ValueError('Unexpected Gemini grounding or URL context')
    usage = response.get('usageMetadata', {})
    if not isinstance(usage, dict) or usage.get('toolUsePromptTokenCount') or usage.get('toolUsePromptTokensDetails'):
        raise ValueError('Unexpected Gemini tool usage')

    content = candidate.get('content')
    if not isinstance(content, dict) or content.get('role') != 'model':
        raise ValueError('Invalid Gemini content')
    parts = content.get('parts')
    if not isinstance(parts, list) or not parts:
        raise ValueError('Missing Gemini text')
    chunks = []
    for part in parts:
        if (not isinstance(part, dict) or set(part) - {'text', 'thought', 'thoughtSignature'}
                or not isinstance(part.get('text'), str) or type(part.get('thought', False)) is not bool):
            raise ValueError('Gemini returned unsupported content')
        if not part.get('thought'):
            chunks.append(part['text'])
    result = decode(''.join(chunks))
    if not Draft202012Validator(schema).is_valid(result):
        raise ValueError('Gemini result did not match the requested schema')
    return result, ['agent_message']


def run(payload, prompt, schema_path, state, model):
    credential = Store(state).read('gemini_credentials') or {}
    key = credential.get('api_key')
    if not isinstance(key, str) or not key.isascii() or not key.isprintable() or not key.strip():
        raise ValueError('Configure the Gemini API key in gemini_credentials')
    schema = json.loads(schema_path.read_text())
    body = json.dumps({
        'systemInstruction': {'parts': [{'text': prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}],
        'generationConfig': {'responseMimeType': 'application/json', 'responseJsonSchema': schema},
    }, allow_nan=False).encode()
    # One charged attempt. HTTPSConnection neither follows redirects nor retries.
    path = '/v1beta/models/' + model_id(model) + ':generateContent'
    try:
        with closing(http.client.HTTPSConnection(HOST, timeout=180)) as connection:
            connection.request('POST', path, body, {'Content-Type': 'application/json', 'x-goog-api-key': key})
            with connection.getresponse() as response:
                if response.status != 200:
                    raise RuntimeError(f'Gemini request failed (HTTP {response.status})')
                raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (http.client.HTTPException, OSError, ValueError):
        raise RuntimeError('Gemini connection failed') from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('Gemini response exceeds the size limit')
    try:
        return structured_result(json.loads(raw), schema)
    except (ValueError, RecursionError, StorageError):
        raise ValueError('Gemini returned an invalid or unsupported structured response') from None
