"""Validates REQ-VXVUEUSAGE-010 with synthetic settings; never opens SSH/network."""
import importlib.util
import io
import inspect
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/vxvue_usage_local.py'

class LocalUsageTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(),'secure owner helper must exist')
        spec=importlib.util.spec_from_file_location('usage_local_under_test',SCRIPT)
        self.mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=self.mod;spec.loader.exec_module(self.mod)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.config=self.root/'transport.json'
        self.value={'server_url':'http://10.23.4.5:24357','ssh_host':'10.23.4.5','ssh_user':'ubuntu','local_port':24359,'remote_port':24357}
        self.config.write_text(json.dumps(self.value),encoding='utf-8')
        self.creds=self.root/'credentials.json'
        self.creds.write_text(json.dumps({self.value['server_url']:{'token':'SYNTHETIC_OWNER_TOKEN_12345678901234567890'}}),encoding='utf-8')

    def test_loopback_forward_and_no_remote_bind(self):
        cfg=self.mod.load_config(self.config)
        args=self.mod.ssh_arguments(cfg)
        self.assertIn('127.0.0.1:24359:127.0.0.1:24357',args)
        self.assertNotIn('-g',args)
        self.assertIn('BatchMode=yes',args)
        self.assertIn('ExitOnForwardFailure=yes',args)

    def test_untrusted_targets_and_ports_rejected(self):
        for field,value in [('ssh_host','-oProxyCommand=bad'),('ssh_user','user;bad'),('local_port',True),('local_port',80),('server_url','http://user:pass@10.23.4.5:24357')]:
            with self.subTest(field=field):
                bad={**self.value,field:value};self.config.write_text(json.dumps(bad),encoding='utf-8')
                with self.assertRaises(ValueError):self.mod.load_config(self.config)

    def test_owner_rpc_payload_is_posted_only_to_localhost(self):
        cfg=self.mod.load_config(self.config);opener=Mock()
        opener.open.side_effect=[self.owner_tools_response(),io.BytesIO(json.dumps({'jsonrpc':'2.0','id':1,'result':{'content':[{'type':'text','text':'ok'}],'isError':False}}).encode())]
        with patch.object(self.mod,'ensure_tunnel') as tunnel:
            self.mod.call_owner('begin_vxvue_activity',{'question':'SYNTHETIC_PRIVATE_QUESTION'},cfg,credential_file=self.creds,opener=opener)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'http://127.0.0.1:24359/vxvue-desktop/rpc')
        self.assertEqual(request.get_method(),'POST')
        self.assertIn('SYNTHETIC_PRIVATE_QUESTION',request.data.decode())
        self.assertNotIn('SYNTHETIC_PRIVATE_QUESTION',request.full_url)
        self.assertEqual(request.get_header('Authorization'),'Bearer SYNTHETIC_OWNER_TOKEN_12345678901234567890')
        tunnel.assert_called_once()

    def owner_tools_response(self):
        return io.BytesIO(json.dumps({'jsonrpc':'2.0','id':0,'result':{'tools':[{'name':name} for name in self.mod.OWNER_TOOLS]}}).encode())

    def test_owner_permission_is_checked_before_sending_original(self):
        cfg=self.mod.load_config(self.config);opener=Mock()
        opener.open.side_effect=[self.owner_tools_response(),io.BytesIO(json.dumps({'jsonrpc':'2.0','id':1,'result':{'content':[]}}).encode())]
        with patch.object(self.mod,'ensure_tunnel'):
            self.mod.call_owner('begin_vxvue_activity',{'question':'SYNTHETIC_PRIVATE_QUESTION'},cfg,credential_file=self.creds,opener=opener)
        first=opener.open.call_args_list[0].args[0]
        self.assertEqual(json.loads(first.data)['method'],'tools/list')
        self.assertNotIn('SYNTHETIC_PRIVATE_QUESTION',first.data.decode())
        self.assertEqual(opener.open.call_count,2)

    def test_team_or_revoked_permission_never_sends_original(self):
        cfg=self.mod.load_config(self.config)
        for result in ({'tools':[{'name':'get_status'}]},{'tools':[]},{'tools':[{'name':'begin_vxvue_activity'}]}):
            with self.subTest(result=result):
                opener=Mock();opener.open.return_value=io.BytesIO(json.dumps({'jsonrpc':'2.0','id':0,'result':result}).encode())
                with patch.object(self.mod,'ensure_tunnel'),self.assertRaises(ValueError):
                    self.mod.call_owner('begin_vxvue_activity',{'question':'SYNTHETIC_PRIVATE_QUESTION'},cfg,credential_file=self.creds,opener=opener)
                self.assertEqual(opener.open.call_count,1)
                self.assertNotIn('SYNTHETIC_PRIVATE_QUESTION',opener.open.call_args.args[0].data.decode())

    def test_private_helper_never_forwards_general_tools(self):
        cfg=self.mod.load_config(self.config)
        with self.assertRaises(ValueError):self.mod.call_owner('get_document',{'document_id':'private'},cfg,credential_file=self.creds)

    def test_transport_error_never_echoes_token_or_body(self):
        cfg=self.mod.load_config(self.config);opener=Mock();opener.open.side_effect=OSError('SYNTHETIC_OWNER_TOKEN_12345678901234567890 SYNTHETIC_PRIVATE_QUESTION')
        with patch.object(self.mod,'ensure_tunnel'):
            with self.assertRaises(ValueError) as error:self.mod.call_owner('begin_vxvue_activity',{'question':'SYNTHETIC_PRIVATE_QUESTION'},cfg,credential_file=self.creds,opener=opener)
        self.assertNotIn('SYNTHETIC_',str(error.exception))

    def test_busy_unknown_listener_is_preserved(self):
        cfg=self.mod.load_config(self.config)
        with patch.object(self.mod,'is_listening',return_value=True),patch.object(self.mod,'spawn_tunnel') as spawn:
            with self.assertRaises(ValueError):self.mod.ensure_tunnel(cfg,state_dir=self.root)
        spawn.assert_not_called()

    def test_original_text_requires_explicit_selection(self):
        functions={}
        class Server:
            def __init__(self,*args,**kwargs):pass
            def tool(self,**kwargs):
                def register(fn):functions[fn.__name__]=fn;return fn
                return register
            def run(self,**kwargs):pass
        sdk={'mcp.server.fastmcp':types.SimpleNamespace(FastMCP=Server),
             'mcp.types':types.SimpleNamespace(CallToolResult=Mock,TextContent=Mock,ToolAnnotations=Mock)}
        with patch.dict(sys.modules,sdk),patch.object(self.mod,'call_owner',return_value={'content':[]}) as call:
            self.mod.serve(self.mod.load_config(self.config))
            begin=functions['begin_vxvue_activity']
            inspect.signature(begin,eval_str=True)
            begin(question='SYNTHETIC_PRIVATE_QUESTION')
            self.assertIs(call.call_args.args[1]['save_original'],False)
            begin(question='SYNTHETIC_PRIVATE_QUESTION',save_original=True)
            self.assertIs(call.call_args.args[1]['save_original'],True)

if __name__=='__main__':unittest.main()
