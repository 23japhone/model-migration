"""获知外部到内部请求 ID 映射，以及调度器处理停止条件后的输出。"""
from . import state


def input_processor(cls):
    original = cls.assign_request_id
    def assign(request):
        result = original(request)
        folder = state.active()
        if folder:
            try:
                intent = state.read(folder / 'intent.json')
                expected = (state.read(folder / 'frontend.json')['external_id'] if intent.get('session_mode')
                            else 'cmpl-' + intent['request_id'] + '-0')
                if request.external_req_id == expected:
                    state.write(folder / 'request.json', {'internal_id': request.request_id,
                                'external_id': request.external_req_id, 'prompt_ids': list(request.prompt_token_ids)})
            except Exception as error:
                state.fail(folder, error)
        return result
    cls.assign_request_id = staticmethod(assign)


def scheduler(cls):
    init, update = cls.__init__, cls._update_request_with_output
    def initialize(self, *args, **kwargs):
        init(self, *args, **kwargs)
        state.ready('engine', hooks=['Scheduler._update_request_with_output'])
    def accept(self, request, new_token_ids):
        result = update(self, request, new_token_ids)
        folder, internal = state.mapped()
        if request.request_id == internal and result[0]:
            try:
                index = request.num_output_tokens - 1
                pred = state.read(folder / f'prediction-{index}.json')
                if len(result[0]) != 1 or pred['internal_id'] != internal:
                    raise ValueError('调度器采样身份不一致')
                state.write(folder / f'accepted-{index}.json', {'internal_id': internal, 'output_index': index,
                            'forward_id': pred['forward_id'], 'token_id': result[0][0],
                            'finish_reason': str(request.get_finished_reason()) if result[1] else None})
            except Exception as error:
                state.fail(folder, error)
        return result
    cls.__init__, cls._update_request_with_output = initialize, accept
