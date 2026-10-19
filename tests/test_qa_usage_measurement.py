from scripts.measure_qa_usage import summarize_usage

def test_measurement_does_not_double_count_thinking():
    event = {'type':'result','is_error':False,'usage':{'input_tokens':8,'cache_creation_input_tokens':142217,'cache_read_input_tokens':424751,'output_tokens':14257,'output_tokens_details':{'thinking_tokens':3848}}}
    result = summarize_usage([{'type':'assistant','message':{'usage':event['usage']}},event])
    assert result['total_tokens'] == 581233
    assert result['thinking_tokens'] == 3848
    assert result['calls'] == 1

def test_prepare_measurement_never_starts_cli(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k: (_ for _ in ()).throw(AssertionError('CLI')))
    assert summarize_usage([])['calls'] == 0
