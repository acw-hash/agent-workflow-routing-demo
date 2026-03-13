from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from azure.identity.aio import DefaultAzureCredential

from ..config import Settings
from ..models import FoundryReply


logger = logging.getLogger(__name__)


class FoundryWorkflowClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        self._assistants_cache: dict[str, str] = {}
        self._threads_cache: dict[tuple[str, str], str] = {}
        self._assistants_api_version = "2025-05-01"

    def _project_endpoint(self) -> str:
        if self._settings.foundry_project_endpoint:
            return self._settings.foundry_project_endpoint.rstrip("/")

        if self._settings.foundry_resource_name and self._settings.foundry_project_name:
            resource = self._settings.foundry_resource_name.strip()
            project = self._settings.foundry_project_name.strip()
            return f"https://{resource}.services.ai.azure.com/api/projects/{project}"

        return ""

    def _candidate_endpoints(self) -> list[str]:
        candidates: list[str] = []

        if self._settings.foundry_workflow_endpoint:
            candidates.append(self._settings.foundry_workflow_endpoint.strip())

        project_endpoint = self._project_endpoint()
        workflow = self._settings.foundry_workflow_name.strip()
        api_version = self._settings.foundry_workflow_api_version.strip()

        if project_endpoint and workflow:
            base_paths = [
                f"/flows/{workflow}:invoke",
                f"/flows/{workflow}/invoke",
                f"/workflows/{workflow}:invoke",
                f"/workflows/{workflow}/invoke",
            ]

            for path in base_paths:
                candidates.append(f"{project_endpoint}{path}?api-version={api_version}")
                candidates.append(f"{project_endpoint}{path}")

        deduped: list[str] = []
        seen: set[str] = set()
        for endpoint in candidates:
            if endpoint and endpoint not in seen:
                deduped.append(endpoint)
                seen.add(endpoint)

        return deduped

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        endpoint: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await client.request(method, endpoint, headers=headers, json=json_body)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _assistant_message_text(message_item: dict[str, Any]) -> str:
        content = message_item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                text_block = item.get("text") if isinstance(item, dict) else None
                if isinstance(text_block, dict):
                    value = text_block.get("value")
                    if isinstance(value, str):
                        parts.append(value)
                elif isinstance(text_block, str):
                    parts.append(text_block)
            return "\n".join(part for part in parts if part)
        return ""

    async def _ensure_assistant_for_agent(
        self,
        client: httpx.AsyncClient,
        project_endpoint: str,
        headers: dict[str, str],
        agent_name: str,
    ) -> str:
        if agent_name in self._assistants_cache:
            return self._assistants_cache[agent_name]

        assistants_url = f"{project_endpoint}/assistants?api-version={self._assistants_api_version}"
        assistants = await self._request_json(client, "GET", assistants_url, headers)
        for item in assistants.get("data", []):
            metadata = item.get("metadata") or {}
            if metadata.get("source_agent_name") == agent_name:
                assistant_id = item["id"]
                self._assistants_cache[agent_name] = assistant_id
                return assistant_id

        agent_url = (
            f"{project_endpoint}/agents/{agent_name}?"
            f"api-version={self._settings.foundry_workflow_api_version}"
        )
        agent = await self._request_json(client, "GET", agent_url, headers)
        definition = agent.get("versions", {}).get("latest", {}).get("definition", {})

        model = definition.get("model", "gpt-4.1")
        instructions = definition.get("instructions", "You are a helpful assistant.")
        tools = definition.get("tools", [])

        assistant_tools: list[dict[str, Any]] = []
        tool_resources: dict[str, Any] = {}
        for tool in tools:
            if tool.get("type") == "file_search":
                assistant_tools.append({"type": "file_search"})
                vector_store_ids = tool.get("vector_store_ids") or []
                if vector_store_ids:
                    tool_resources["file_search"] = {"vector_store_ids": vector_store_ids}

        create_payload: dict[str, Any] = {
            "name": f"bridge-{agent_name}",
            "model": model,
            "instructions": instructions,
            "metadata": {
                "managed_by": "policy-chatbot-bridge",
                "source_agent_name": agent_name,
            },
        }
        if assistant_tools:
            create_payload["tools"] = assistant_tools
        if tool_resources:
            create_payload["tool_resources"] = tool_resources

        created = await self._request_json(client, "POST", assistants_url, headers, create_payload)
        assistant_id = created["id"]
        self._assistants_cache[agent_name] = assistant_id
        return assistant_id

    async def _run_assistant(
        self,
        client: httpx.AsyncClient,
        project_endpoint: str,
        headers: dict[str, str],
        assistant_id: str,
        session_id: str,
        user_query: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        cache_key = (assistant_id, session_id)
        thread_id = self._threads_cache.get(cache_key, "")
        is_new_thread = False

        if not thread_id:
            thread = await self._request_json(
                client,
                "POST",
                f"{project_endpoint}/threads?api-version={self._assistants_api_version}",
                headers,
                {},
            )
            thread_id = thread["id"]
            self._threads_cache[cache_key] = thread_id
            is_new_thread = True

        if is_new_thread and history:
            # Seed the assistant thread with recent context so session continuity survives restarts.
            for item in history[-8:]:
                role = item.get("role")
                content = item.get("content", "")
                if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
                    continue
                await self._request_json(
                    client,
                    "POST",
                    f"{project_endpoint}/threads/{thread_id}/messages?api-version={self._assistants_api_version}",
                    headers,
                    {"role": role, "content": content.strip()},
                )

        await self._request_json(
            client,
            "POST",
            f"{project_endpoint}/threads/{thread_id}/messages?api-version={self._assistants_api_version}",
            headers,
            {"role": "user", "content": user_query},
        )

        run = await self._request_json(
            client,
            "POST",
            f"{project_endpoint}/threads/{thread_id}/runs?api-version={self._assistants_api_version}",
            headers,
            {"assistant_id": assistant_id},
        )
        run_id = run["id"]

        for _ in range(45):
            run_status = await self._request_json(
                client,
                "GET",
                f"{project_endpoint}/threads/{thread_id}/runs/{run_id}?api-version={self._assistants_api_version}",
                headers,
            )
            status = run_status.get("status", "")
            if status == "completed":
                break
            if status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(f"Assistant run failed with status: {status}")
            await asyncio.sleep(2)
        else:
            raise RuntimeError("Assistant run timed out.")

        messages = await self._request_json(
            client,
            "GET",
            f"{project_endpoint}/threads/{thread_id}/messages?api-version={self._assistants_api_version}",
            headers,
        )
        for item in messages.get("data", []):
            if item.get("role") == "assistant":
                text = self._assistant_message_text(item)
                if text:
                    return text

        raise RuntimeError("No assistant response text was returned.")

    async def _invoke_agent_bridge(
        self,
        user_query: str,
        session_id: str,
        history: list[dict[str, str]],
        request_id: str,
    ) -> FoundryReply:
        project_endpoint = self._project_endpoint()
        if not project_endpoint:
            raise RuntimeError("Foundry project endpoint is not configured for assistant bridge mode.")

        headers = await self._build_headers()
        headers["Content-Type"] = "application/json"
        headers["x-request-id"] = request_id
        logger.info(
            "Using assistant bridge request_id=%s session_id=%s history_items=%s",
            request_id,
            session_id,
            len(history),
        )

        timeout = httpx.Timeout(self._settings.foundry_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.info(
                "Invoking assistant bridge routing agent request_id=%s session_id=%s agent=%s",
                request_id,
                session_id,
                "routing-agent",
            )
            routing_assistant = await self._ensure_assistant_for_agent(
                client, project_endpoint, headers, "routing-agent"
            )
            route_label = (
                await self._run_assistant(
                    client,
                    project_endpoint,
                    headers,
                    routing_assistant,
                    session_id=f"{session_id}-routing",
                    user_query=user_query,
                    history=history,
                )
            ).strip()
            lowered = route_label.lower()

            if "fraud" in lowered:
                selected_agent = "fraud-agent"
            elif "refund" in lowered or "dispute" in lowered:
                selected_agent = "refunds-agent"
            else:
                selected_agent = "card-services-agent"
            logger.info(
                "Assistant bridge selected agent request_id=%s session_id=%s route_label=%s selected_agent=%s",
                request_id,
                session_id,
                route_label,
                selected_agent,
            )

            domain_assistant = await self._ensure_assistant_for_agent(
                client, project_endpoint, headers, selected_agent
            )
            answer = await self._run_assistant(
                client,
                project_endpoint,
                headers,
                domain_assistant,
                session_id=session_id,
                user_query=user_query,
                history=history,
            )
            logger.info(
                "Assistant bridge agent completed request_id=%s session_id=%s selected_agent=%s",
                request_id,
                session_id,
                selected_agent,
            )

        return FoundryReply(
            text=answer,
            raw={
                "mode": "assistant-bridge",
                "route_label": route_label,
                "selected_agent": selected_agent,
            },
        )

    async def _build_headers(self) -> dict[str, str]:
        if self._settings.foundry_api_key:
            return {"api-key": self._settings.foundry_api_key}

        token = await self._credential.get_token(self._settings.foundry_scope)
        return {"Authorization": f"Bearer {token.token}"}

    async def close(self) -> None:
        await self._credential.close()

    async def ask_workflow(
        self,
        user_query: str,
        session_id: str,
        user_id: str,
        domain: str,
        policy_context: str,
        history: list[dict[str, str]],
        request_id: str,
    ) -> FoundryReply:
        candidate_endpoints = self._candidate_endpoints()
        logger.info(
            "Preparing Foundry invocation request_id=%s session_id=%s user_id=%s domain=%s candidate_endpoints=%s has_project_endpoint=%s",
            request_id,
            session_id,
            user_id,
            domain,
            len(candidate_endpoints),
            bool(self._project_endpoint()),
        )
        if not candidate_endpoints and self._project_endpoint():
            logger.info(
                "No workflow endpoints resolved; falling back to assistant bridge request_id=%s session_id=%s",
                request_id,
                session_id,
            )
            return await self._invoke_agent_bridge(
                user_query=user_query,
                session_id=session_id,
                history=history,
                request_id=request_id,
            )
        if not candidate_endpoints:
            raise RuntimeError("Foundry endpoint configuration is missing.")

        headers = await self._build_headers()
        headers["Content-Type"] = "application/json"
        headers["x-request-id"] = request_id

        payload: dict[str, Any] = {
            "input": {
                "query": user_query,
                "session_id": session_id,
                "user_id": user_id,
                "domain": domain,
                "policy_context": policy_context,
                "history": history,
            }
        }

        timeout = httpx.Timeout(self._settings.foundry_timeout_seconds)
        last_error: str | None = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            for endpoint in candidate_endpoints:
                try:
                    logger.info(
                        "Attempting direct workflow invoke request_id=%s session_id=%s endpoint=%s",
                        request_id,
                        session_id,
                        endpoint,
                    )
                    response = await client.post(endpoint, headers=headers, json=payload)
                    if response.status_code >= 400:
                        last_error = f"{response.status_code} from {endpoint}"
                        logger.warning(
                            "Direct workflow invoke returned error request_id=%s session_id=%s endpoint=%s status_code=%s",
                            request_id,
                            session_id,
                            endpoint,
                            response.status_code,
                        )
                        continue

                    response.raise_for_status()
                    body = response.json()
                    logger.info(
                        "Direct workflow invoke succeeded request_id=%s session_id=%s endpoint=%s response_keys=%s",
                        request_id,
                        session_id,
                        endpoint,
                        ",".join(sorted(body.keys())),
                    )
                    break
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    last_error = f"{status_code} from {endpoint}: {exc.response.text}"
                    logger.warning(
                        "Direct workflow invoke raised HTTPStatusError request_id=%s session_id=%s endpoint=%s status_code=%s",
                        request_id,
                        session_id,
                        endpoint,
                        status_code,
                    )
                    continue
                except Exception as exc:  # pylint: disable=broad-except
                    last_error = f"Error at {endpoint}: {exc}"
                    logger.warning(
                        "Direct workflow invoke raised exception request_id=%s session_id=%s endpoint=%s error=%s",
                        request_id,
                        session_id,
                        endpoint,
                        exc,
                    )
                    continue
            else:
                if self._project_endpoint():
                    logger.warning(
                        "All direct workflow invoke attempts failed; falling back to assistant bridge request_id=%s session_id=%s last_error=%s",
                        request_id,
                        session_id,
                        last_error,
                    )
                    return await self._invoke_agent_bridge(
                        user_query=user_query,
                        session_id=session_id,
                        history=history,
                        request_id=request_id,
                    )
                raise RuntimeError(
                    "Unable to invoke Foundry workflow. "
                    f"Tried endpoints: {candidate_endpoints}. Last error: {last_error}"
                )

        text = (
            body.get("response")
            or body.get("output")
            or body.get("result")
            or body.get("answer")
            or "I could not produce a response from the Foundry workflow."
        )

        return FoundryReply(
            text=str(text),
            raw={
                "mode": "workflow-direct",
                "endpoint": endpoint,
                "body": body,
            },
        )
