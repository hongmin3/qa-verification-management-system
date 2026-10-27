"""Creator-only recording. Identity is supplied exclusively by server authentication."""
import uuid
from . import telemetry

OWNER_INSTRUCTIONS='''본인 선택 이력 기능입니다. 사용자가 기록할 업무를 선택한 경우 begin_vxvue_activity로 업무 ID를 받고 조회 도구의 usage_context.activity_id에 연결하세요. 원문 저장을 선택한 경우에만 save_original=true와 본인의 원질문을 전달합니다. 최종 답변 작성 뒤 finish_vxvue_activity로 최종 답변·활용 요약을 남깁니다. 최종 답변이 없으면 INCOMPLETE, 실패하면 FAILED로 기록합니다. 도구 성공을 정확성이나 활용 완료로 바꾸지 않습니다. 기록 안내를 일반 답변에 덧붙이지 않습니다.'''


def definitions():
    string={'type':'string'}
    values=[('begin_vxvue_activity','본인이 선택한 업무만 시작한다. 원문은 save_original=true를 선택한 경우만 저장한다.',{'activity_id':string,'feature':{'type':'string','enum':['documents','specification','testcase','issue','manual','generator','other']},'question':{'type':'string','maxLength':30000},'save_original':{'type':'boolean'}},[]),
            ('finish_vxvue_activity','같은 제작자 연결의 선택 업무에 최종 답변·활용 요약 또는 미완료/실패를 기록한다.',{'activity_id':string,'answer':{'type':'string','maxLength':30000},'summary':{'type':'string','maxLength':12000},'status':{'type':'string','enum':['ANSWER_RECORDED','INCOMPLETE','FAILED','CANCELLED']}},['activity_id'])]
    return [{'name':n,'description':d,'inputSchema':{'type':'object','properties':p,'required':r,'additionalProperties':False},'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False}} for n,d,p,r in values]


def decorate(base):
    import copy
    values=copy.deepcopy(base)+definitions()
    for value in values:
        value['inputSchema']['properties']['usage_context']={'type':'object','properties':{'activity_id':{'type':'string','format':'uuid'},'event_id':{'type':'string','format':'uuid'},'attempt':{'type':'integer','minimum':1,'maximum':20}},'additionalProperties':False}
    return values



def owner_call(name,arguments,context):
    if not context.get('owner') or not context.get('secure') or not context.get('binding'):raise ValueError('이 연결은 본인 업무를 기록할 수 없습니다.')
    args=dict(arguments);args.pop('usage_context',None)
    target=telemetry.store(root=context.get('root'))
    if name=='begin_vxvue_activity':
        activity_id=args.pop('activity_id',None) or str(uuid.uuid4())
        activity_id=target.usage_start(activity_id,context['binding'],context['channel'],args.pop('feature','other'),source='test' if context.get('diagnostic') else 'live',**args)
        return {'activity_id':activity_id,'status':target.usage_get(activity_id)['status']}
    if name=='finish_vxvue_activity':
        activity_id=args.pop('activity_id')
        if not target.usage_finish(activity_id,context['binding'],**args):raise ValueError('이 연결의 업무를 찾을 수 없습니다.')
        return {'activity_id':activity_id,'status':args.get('status','ANSWER_RECORDED')}
    raise ValueError('허용되지 않은 기록 도구입니다.')
