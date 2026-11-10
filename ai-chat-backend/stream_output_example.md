responses.create(stream=True) 一次真实调用的收尾事件序列：
```
ResponseTextDeltaEvent(content_index=0, delta='路线', item_id='98fb1e61-0ba0-491d-aff4-f3d302b6b20', logprobs=[], output_index=0, sequence_number=597, type='response.output_text.delta')

ResponseTextDeltaEvent(content_index=0, delta='。', item_id='98fb1e61-0ba0-491d-aff4-f3d302b6b20', logprobs=[], output_index=0, sequence_number=598, type='response.output_text.delta')

ResponseTextDoneEvent(content_index=0, item_id='98fb1e61-0ba0-491d-aff4-f3d302b6b20', logprobs=[], output_index=0, sequence_number=599, text='xxxxx', type='response.output_text.done')

ResponseContentPartDoneEvent(content_index=0, item_id='98fb1e61-0ba0-491d-aff4-f3d302b6b20', output_index=0, part=ResponseOutputText(annotations=[], text='xxxxx', type='output_text', logprobs=[]), sequence_number=600, type='response.content_part.done')

ResponseOutputItemDoneEvent(item=ResponseOutputMessage(id='98fb1e61-0ba0-491d-aff4-f3d302b6b20', content=[ResponseOutputText(annotations=[], text='xxxxx', type='output_text', logprobs=[])], role='assistant', status='completed', type='message', phase='final_answer'), output_index=0, sequence_number=601, type='response.output_item.done')

ResponseCompletedEvent(response=Response(id='e7dfc9f0-3093-4853-beca-847d99ce53d7', created_at=1794295173.0, error=None, incomplete_details=None, instructions=None, metadata={}, model='deepseek-flash', object='response', output=[ResponseOutputMessage(id='98fb1e61-0ba0-491d-aff4-f3d302b6b20', content=[ResponseOutputText(annotations=[], text='xxxxx', type='output_text', logprobs=[])], role='assistant', status='completed', type='message', phase='final_answer')], parallel_tool_calls=True, temperature=1.0, tool_choice='auto', tools=[], top_p=1.0, background=False, completed_at=1794295179.0, conversation=None, max_output_tokens=None, max_tool_calls=None, moderation=None, previous_response_id=None, prompt=None, prompt_cache_key=None, prompt_cache_retention=None, reasoning=Reasoning(context=None, effort='none', generate_summary=None, summary=None), safety_identifier=None, service_tier='default', status='completed', text=ResponseTextConfig(format=ResponseFormatText(type='text'), verbosity=None), top_logprobs=0, truncation='disabled', usage=ResponseUsage(input_tokens=53, input_tokens_details=InputTokensDetails(cached_tokens=0), output_tokens=595, output_tokens_details=OutputTokensDetails(reasoning_tokens=0), total_tokens=648), user=None, content_filters=None, frequency_penalty=0.0, presence_penalty=0.0, store=False), sequence_number=602, type='response.completed')
```

| seq | type | 关键 payload | 代码中的处理 |
| --- | --- | --- | --- |
| 597/598 | response.output_text.delta | delta='路线'/'。' 增量碎片;item_id/content_index/output_index 定位归属 | yield 给 SSE(strategy.py:106-107) |
| 599 | response.output_text.done | text= 该文本 part 的完整拼接结果(=所有 delta 之和) | 忽略(增量已累加,无需重读) |
| 600 | response.content_part.done | part=ResponseOutputText(...) 关闭第 0 个 content part | 忽略 |
| 601 | response.output_item.done | item=ResponseOutputMessage(role='assistant', status='completed', phase='final_answer') 关闭整条输出消息 | 忽略 |
| 602 | response.completed | response=Response(...) 完整响应对象,唯一带 usage 的事件 | 捕获 usage(strategy.py:108-109) |

```
usage = ResponseUsage(
    input_tokens=53,                                    # prompt(system+历史+本轮提问)
    input_tokens_details=InputTokensDetails(cached_tokens=0),   # 无 prompt 缓存命中
    output_tokens=595,                                  # 生成的回答(那段 markdown)
    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),  # 思考关闭,无推理 token
    total_tokens=648,                                   # = 53 + 595,自洽
)
```
- total = input + output(648 = 53+595),校验通过
- reasoning.effort='none' 与 reasoning_tokens=0 对应:思考模式关闭。若开启思考，reasoning_tokens>0 且计入 output_tokens 内部，不会额外加到 total
- cached_tokens=0:本轮没吃到 prompt 缓存