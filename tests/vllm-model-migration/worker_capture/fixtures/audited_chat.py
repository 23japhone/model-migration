# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""目标 chat serving 的挂点协议片段，固定单输入/greedy/非流式条件。

来源镜像 sha256:beb23b3674451cbe378e87bcf0ea45a8a156a16a5f026804e8d8b837164b4b71
来源模块 vllm.entrypoints.openai.chat_completion.serving
完整源文件 SHA256: 8c41af967ef813bf6fd2e2f16b8eedbbf6098d78c971639a58b6297d1c217c02
摘录保留 request ID、generate 的位置/关键字协议及最终响应等待。
render、beam、流式、parser 分支不属于本测试；不是完整 SDK 或真实推理。
"""


class OpenAIServingChat:
    async def create_chat_completion(self, request, raw_request=None):
        tokenizer = self.renderer.tokenizer
        chat_template_kwargs = self._effective_chat_template_kwargs(request)
        conversation, engine_inputs = await self.render_chat_request(request)
        request_id = f"chatcmpl-{self._base_request_id(raw_request, request.request_id)}"
        request_metadata = RequestResponseMetadata(request_id=request_id)
        if raw_request: raw_request.state.request_metadata = request_metadata
        lora_request = self._maybe_get_adapters(request, supports_default_mm_loras=True)
        model_name = self.models.model_name(lora_request)
        data_parallel_rank = self._get_data_parallel_rank(raw_request)
        engine_input, = engine_inputs
        sub_request_id = request_id
        sampling_params = request.to_sampling_params(2, self.default_sampling_params)
        trace_headers = await self._get_trace_headers(raw_request.headers)
        reasoning_ended, reasoning_parser = True, None
        generator = self.engine_client.generate(
            engine_input,
            sampling_params,
            sub_request_id,
            lora_request=lora_request,
            trace_headers=trace_headers,
            priority=request.priority,
            data_parallel_rank=data_parallel_rank,
            reasoning_ended=reasoning_ended,
            reasoning_parser_kwargs={
                "chat_template_kwargs": chat_template_kwargs,
            }
            if reasoning_parser
            else None,
        )
        return await self.chat_completion_full_generator(
            request, generator, request_id, model_name, conversation,
            tokenizer, request_metadata, reasoning_parser,
        )
