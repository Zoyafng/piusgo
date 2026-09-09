'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {api,ApiError,errorText} from '@/lib/api';
export type ChatMessage={id:string;seq:number;sender_role:'customer'|'agent';body:string;attachment_id:string|null;created:number;client_id:string};
export type Conversation={unread:number;my_read_seq:number;id:string;status:'waiting'|'active'|'closed';last_seq:number;contact_email:string;order_id:string|null;ticket_id:string|null;assigned_to:string|null;other_read_seq:number;online:boolean;customer_name?:string;order:{id:string;product_name:string;variant_name:string;total:number;status:string}|null};
type Packet={conversation:Conversation;messages:ChatMessage[];cursor:number;has_older:boolean};
export function useChat(cid:string|null,actor:'customer'|'agent',active:boolean,revoked:()=>void){
  const [messages,setMessages]=useState<ChatMessage[]>([]),[conversation,setConversation]=useState<Conversation|null>(null),[connection,setConnection]=useState('连接中'),[error,setError]=useState(''),[older,setOlder]=useState(false),[visible,setVisible]=useState(true);
  const cursor=useRef(0),current=useRef(cid),onRevoked=useRef(revoked),lastRead=useRef(0);onRevoked.current=revoked;current.current=cid;
  const merge=useCallback((items:ChatMessage[])=>{setMessages(previous=>{const all=new Map(previous.map(m=>[m.seq,m]));for(const m of items)all.set(m.seq,m);return [...all.values()].sort((a,b)=>a.seq-b.seq);});for(const m of items)cursor.current=Math.max(cursor.current,m.seq);},[]);
  useEffect(()=>{const update=()=>setVisible(document.visibilityState==='visible');update();document.addEventListener('visibilitychange',update);return()=>document.removeEventListener('visibilitychange',update);},[]);
  useEffect(()=>{setMessages([]);setConversation(null);setError('');setOlder(false);cursor.current=0;lastRead.current=0;if(!cid)return;let running=true,source:EventSource|undefined,timer:ReturnType<typeof setTimeout>|undefined;
    function denied(e:unknown){if(e instanceof ApiError&&(e.status===401||e.status===403||e.status===404)){source?.close();onRevoked.current();return true;}return false;}
    async function fallback(){if(!running)return;try{const packet=await api<Packet>(`/support/conversations/${cid}/messages?actor=${actor}&after=${cursor.current}`);if(!running)return;merge(packet.messages);setConversation(packet.conversation);setError('');setConnection('轮询恢复中');}catch(e){if(!running||denied(e))return;setError(errorText(e));}if(running&&source?.readyState!==EventSource.OPEN)timer=setTimeout(fallback,5000);}
    async function connect(){try{const packet=await api<Packet>(`/support/conversations/${cid}/messages?actor=${actor}`);if(!running)return;setMessages(packet.messages);setConversation(packet.conversation);setOlder(packet.has_older);cursor.current=packet.cursor;
      source=new EventSource(`/api/support/conversations/${cid}/events?actor=${actor}&after=${cursor.current}`);
      source.onopen=()=>{if(running){setConnection('实时连接');setError('');if(timer)clearTimeout(timer);}};
      source.addEventListener('message',e=>{if(running)merge([JSON.parse((e as MessageEvent).data)]);});
      source.addEventListener('state',e=>{if(running)setConversation(JSON.parse((e as MessageEvent).data));});
      source.addEventListener('revoked',()=>{source?.close();if(running)onRevoked.current();});
      source.onerror=()=>{if(!running)return;setConnection('正在重连');if(timer)clearTimeout(timer);timer=setTimeout(fallback,3000);};
    }catch(e){if(!running||denied(e))return;setError(errorText(e));setConnection('连接失败');timer=setTimeout(connect,5000);}}
    void connect();return()=>{running=false;source?.close();if(timer)clearTimeout(timer);};
  },[cid,actor,merge]);
  useEffect(()=>{const latest=messages.at(-1)?.seq||0;if(!cid||!active||!visible||latest<=lastRead.current)return;let alive=true;api(`/support/conversations/${cid}/read?actor=${actor}`,{last_seq:latest}).then(()=>{if(alive)lastRead.current=latest;}).catch(()=>{});return()=>{alive=false;};},[cid,actor,active,visible,messages]);
  async function send(body:string,client_id:string,attachment_id:string|null){if(!cid)throw new Error('会话尚未就绪');const result=await api<ChatMessage>(`/support/conversations/${cid}/messages?actor=${actor}`,{body,client_id,attachment_id});if(current.current===cid)merge([result]);}
  async function loadOlder(){if(!cid||!messages.length)return;const old=cid;const packet=await api<Packet>(`/support/conversations/${cid}/messages?actor=${actor}&before=${messages[0].seq}`);if(current.current!==old)return;merge(packet.messages);setOlder(packet.has_older);}
  return {messages,conversation,connection,error,older,loadOlder,send};
}
