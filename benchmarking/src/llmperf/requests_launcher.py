from typing import Any, List

from llmperf.ray_llm_client import LLMClient
from llmperf.models import RequestConfig
from ray.util import ActorPool


class RequestsLauncher:
    """Launch requests from LLMClients to their respective LLM APIs."""

    def __init__(self, llm_clients: List[LLMClient]):
        self._llm_client_pool = ActorPool(llm_clients)

    def launch_requests(self, request_config: RequestConfig) -> None:
        """Launch requests to the LLM API.

        Args:
            request_config: The configuration for the request.
        """

        if self._llm_client_pool.has_free():
            self._llm_client_pool.submit(
                lambda client, _request_config: client.llm_request.remote(
                    _request_config
                ),
                request_config,
            )

    def get_next_ready(self, block: bool = False) -> List[Any]:
        """Return results that are ready from completed requests.

        Args:
            block (bool): Whether to block until a result is ready.

        Returns:
            List[Any]: A list of results that are ready.

        """

        results = []
        try:
            if not block:
                while self._llm_client_pool.has_next():
                    result = self._llm_client_pool.get_next_unordered()
                    results.append(result)
            else:
                while not self._llm_client_pool.has_next():
                    pass
                while self._llm_client_pool.has_next():
                    result = self._llm_client_pool.get_next_unordered()
                    results.append(result)
            return results
        except Exception as _:
            error_msg = f"Error ocurred in getting next ray client executed. Please review your endpoint credentials or model/expert name"
            raise Exception(error_msg)
