"""Readback and stream lifecycle regressions at public evidence boundaries."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

VERIFY = next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))


class IntegrityTests(unittest.TestCase):
    def test_lazy_stream_preserves_context_across_distinct_tasks_and_delta(self):
        from verification.runtime.capture.adapters import frontend, state
        class Client:
            async def generate(self, request_id, sampling_params):
                for finished in (False, True):
                    yield NS(request_id=request_id, prompt_token_ids=[3] if not finished else None,
                             outputs=[NS(token_ids=[7], finish_reason='length' if finished else None)],
                             finished=finished)
        class Server:
            def __init__(self): self.engine_client = Client()
            async def serve(self, request, raw_request=None):
                async def stream():
                    async for item in self.engine_client.generate('engine-1', NS(output_kind=NS(name='DELTA'))):
                        yield 'data: ' + json.dumps({'id': 'response-1'}) + '\n\n'
                return stream()
        async def drive(folder):
            with patch.object(state, 'ready'), patch.object(state, 'active', return_value=folder):
                frontend.install(Server, 'serve')
                stream = await Server().serve(None, NS(headers={'X-Precision-Trace':'t'}))
                self.assertFalse((folder/'frontend-end.json').exists())
                await asyncio.create_task(anext(stream))
                await asyncio.create_task(anext(stream))
                with self.assertRaises(StopAsyncIteration):
                    await asyncio.create_task(anext(stream))
            self.assertIsNone(frontend.CURRENT.get())
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state.write(folder/'intent.json', {'session_mode':True, 'trace_id':'t'})
            asyncio.run(drive(folder))
            self.assertEqual(state.read(folder/'engine-output.json')['token_ids'], [7, 7])
            self.assertEqual(state.read(folder/'frontend.json')['response_id'], 'response-1')
            self.assertTrue(state.read(folder/'frontend-end.json')['stream_exhausted'])

    def test_stream_disconnect_drops_uncommitted_event_bytes(self):
        from verification.runtime.capture.generation.delivery import parse_stream
        event = {'id':'a','model':'m','choices':[{'index':0,'token_ids':[7]}]}
        part = b'data: '+json.dumps(event).encode()+b'\n\n'
        response = parse_stream(part+b'data: {"text":"\xe8')
        self.assertEqual(response['choices'][0]['token_ids'], [7])
        self.assertFalse(response['delivery']['complete'])

    def test_changed_image_fails_readback(self):
        from verification.runtime.capture.generation.vision import check_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root/'embedding.bin').write_bytes(b'ab')
            meta={'snapshots':{'embedding':{'file':'embedding.bin','bytes':2,'sha256':hashlib.sha256(b'ab').hexdigest()}}}
            check_files(root, meta)
            (root/'embedding.bin').write_bytes(b'ac')
            with self.assertRaisesRegex(ValueError, '视觉快照损坏'):
                check_files(root, meta)

    def test_seed_and_filter_match_actual_metadata(self):
        from verification.runtime.capture.generation.receipt import matches_sample
        meta = dict(seed=42, temperature=1.2, top_k=8, top_p=.97, candidate=3)
        payload = dict(seed=42, temperature=1.2, top_k=8, top_p=.97)
        self.assertTrue(matches_sample(meta, payload, 3))
        self.assertFalse(matches_sample(meta, {**payload,'seed':43}, 3))
        self.assertFalse(matches_sample(meta, {**payload,'top_k':9}, 3))

    def test_image_origin_and_fusion_chunks_are_distinct(self):
        from verification.runtime.capture.generation.vision import bind_images
        from verification.runtime.capture.parallel.reader import resolve_entry
        from verification.runtime.capture.tensors import file_ref
        from verification.evidence import write_json
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            picture = folder/'image-0.bin'
            picture.write_bytes(b'source')
            feature = dict(offset=2, length=2, image_index=0, identifier='processor')
            timeline = []
            for i, points in enumerate(([2], [3], [])):
                row = dict(forward_id=str(i), output_index=(i-1 if i else None), ranks=[])
                for rank in (0, 1):
                    ref = folder/f'row-{rank}-{i}.json'
                    write_json(ref, {'vision':dict(feature=feature, encoder_executed=i==0, fusion_positions=points, snapshots={})})
                    row['ranks'].append(dict(rank=rank, row=file_ref(ref,folder)))
                timeline.append(row)
            errors = []
            images = bind_images([dict(image_index=0, **file_ref(picture,folder))], timeline, errors, 2)
            self.assertEqual(errors, [])
            self.assertEqual(images[0]['ranks'][1]['fusion_forward_ids'], ['0','1'])
            result = dict(images=images, timeline=timeline, engine_input={'path':str(folder/'request.json')})
            encoder, origin = resolve_entry(result, dict(output_index=1, stack='vision', image_index=0), [1])
            self.assertEqual((origin['text_forward_id'], encoder['forward_id']), ('2', '0'))
            picture.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, '原始图像'):
                resolve_entry(result, dict(output_index=1, stack='vision'), [0])

    def test_disconnect_before_first_engine_result_preserves_absence(self):
        from verification.runtime.capture.session_receipt import mapped_response
        from verification.runtime.capture.adapters import state
        from verification.runtime.capture.tensors import file_ref
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            state.write(folder/'frontend.json', dict(trace_id='t', external_id='x'))
            state.write(folder/'frontend-end.json', dict(trace_id='t', stream_exhausted=False))
            state.write(folder/'request.json', dict(prompt_ids=[1], external_id='x'))
            state.write(folder/'request.raw', dict(stream=True))
            (folder/'response.raw').write_bytes(b'')
            request = dict(trace_id='t', http={k:file_ref(folder/(k+'.raw'), folder) for k in ('request','response')})
            mapped, engine, response = mapped_response(folder, request)
            self.assertFalse(engine['finished'])
            self.assertEqual(response['delivery']['events'], [])
            self.assertIsNone(response['id'])
            self.assertFalse((folder/'engine-output.json').exists())

    def test_buffered_json_cannot_attest_stream_delivery(self):
        from verification.runtime.capture.generation.delivery import load_response
        from verification.runtime.capture.tensors import file_ref
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root/'input').write_text('{"stream":true}')
            (root/'response').write_text('{"id":"a","choices":[{"token_ids":[7]}]}')
            http = dict(request=file_ref(root/'input', root), response=file_ref(root/'response',root))
            with self.assertRaisesRegex(ValueError, 'SSE'):
                load_response(http)

    def test_failed_vision_digest_charges_bytes_that_were_written(self):
        from contextlib import nullcontext
        from verification.runtime.capture.adapters import image_capture
        class BrokenWatch:
            def __init__(self, directory, *args):
                self.directory = directory
            def boundary(self, name, tensor):
                (self.directory/(name+'.bin')).write_bytes(b'spent')
                raise ValueError('digest mismatch')
        with tempfile.TemporaryDirectory() as tmp, patch.object(image_capture,'Snapshots',BrokenWatch), patch.object(image_capture,'observer_only',nullcontext):
            vision = dict(snapshot_bytes=0, observer_seconds=0, snapshots={})
            rec = NS(cache_selection=True, request_remaining=8, cache_budget=8, cache_watch=None,
                     frame=dict(forward_id='a',vision=vision), folder=Path(tmp).resolve())
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                image_capture.save(rec,'test',None)
            self.assertEqual((rec.request_remaining,vision['snapshot_bytes']), (3,5))
            self.assertEqual(vision['snapshots'], {})
