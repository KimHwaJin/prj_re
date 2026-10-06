"""Asynchronous OpenAI-compatible embeddings; separate from the chat model."""

import asyncio
import math

import httpx
import numpy as np


class EmbeddingUnavailable(RuntimeError):
    pass


def validate_vectors(vectors, count, dimensions):
    if not isinstance(vectors, list) or len(vectors) != count:
        raise EmbeddingUnavailable("embedding_response_invalid")
    clean = []
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
            raise EmbeddingUnavailable("embedding_dimensions_mismatch")
        if any(
            isinstance(x, bool)
            or not isinstance(x, (float, int))
            or not math.isfinite(x)
            for x in vector
        ):
            raise EmbeddingUnavailable("embedding_response_invalid")
        with np.errstate(over="ignore", under="ignore"):
            values = np.asarray(vector, dtype=np.float32)
        if not np.isfinite(values).all():
            raise EmbeddingUnavailable("embedding_response_invalid")
        values = values.tolist()
        if not any(values):
            raise EmbeddingUnavailable("embedding_zero_vector")
        clean.append(values)
    return clean


class OpenAICompatibleEmbedding:
    def __init__(self, settings):
        self.settings = settings
        self._client = None
        self._semaphore = asyncio.Semaphore(settings.embedding_concurrency)

    async def embed(self, texts):
        if not self.settings.configured:
            raise EmbeddingUnavailable("embedding_unconfigured")
        # Includes admission wait and every batch; no invisible SDK retry loop.
        try:
            async with asyncio.timeout(
                self.settings.embedding_timeout_seconds
            ):
                async with self._semaphore:
                    if self._client is None:
                        self._client = httpx.AsyncClient(
                            timeout=self.settings.embedding_timeout_seconds,
                            limits=httpx.Limits(
                                max_connections=self.settings.embedding_concurrency,
                                max_keepalive_connections=self.settings.embedding_concurrency,
                            ),
                        )
                    vectors = []
                    for offset in range(
                        0, len(texts), self.settings.embedding_batch_size
                    ):
                        batch = texts[
                            offset : offset
                            + self.settings.embedding_batch_size
                        ]
                        headers = {}
                        key = self.settings.api_key.get_secret_value()
                        if key:
                            headers["Authorization"] = "Bearer " + key
                        response = await self._client.post(
                            self.settings.base_url.rstrip("/") + "/embeddings",
                            headers=headers,
                            json={
                                "model": self.settings.model,
                                "input": batch,
                            },
                        )
                        response.raise_for_status()
                        data = response.json().get("data")
                        if (
                            not isinstance(data, list)
                            or len(data) != len(batch)
                            or {
                                d.get("index")
                                for d in data
                                if isinstance(d, dict)
                            }
                            != set(range(len(batch)))
                        ):
                            raise EmbeddingUnavailable(
                                "embedding_response_invalid"
                            )
                        vectors.extend(
                            validate_vectors(
                                [
                                    item.get("embedding")
                                    for item in sorted(
                                        data, key=lambda d: d["index"]
                                    )
                                ],
                                len(batch),
                                self.settings.dimensions,
                            )
                        )
                    return vectors
        except (httpx.HTTPError, TimeoutError):
            # Never persist/log transport exceptions containing credentials/URLs.
            raise EmbeddingUnavailable("embedding_transport_failed") from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise EmbeddingUnavailable("embedding_response_invalid") from None

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
