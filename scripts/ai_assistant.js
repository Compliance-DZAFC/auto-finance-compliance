function selectRelevantCases(question,maxChars){
  const q=question.toLowerCase().trim();
  if(!q||q.length<2)return caseSummary.slice(0,10);
  const scored=caseSummary.map(c=>{
    let score=0;
    const text=(c.field+" "+c.sub_field+" "+c.violation+" "+c.advice.join(" ")).toLowerCase();
    for(let i=0;i<q.length;i++){
      const ch=q[i];
      if(/[\u4e00-\u9fa5a-z0-9]/.test(ch)){
        let idx=text.indexOf(ch);
        while(idx!==-1){score+=1;idx=text.indexOf(ch,idx+1);}
      }
    }
    for(let i=0;i<q.length-1;i++){
      const bg=q.slice(i,i+2);
      if(text.includes(bg))score+=5;
    }
    if(q.includes("高")&&c.level==="高")score+=15;
    if(q.includes("中")&&c.level==="中")score+=15;
    if(q.includes("低")&&c.level==="低")score+=15;
    return{...c,score};
  });
  scored.sort((a,b)=>b.score-a.score);
  let chars=0;
  const selected=[];
  for(const c of scored){
    const len=JSON.stringify(c).length;
    if(chars+len>maxChars&&selected.length>0)break;
    selected.push(c);
    chars+=len;
  }
  return selected;
}
async function sendAIQuestion(){
  const input=document.getElementById("aiInput");
  const q=input.value.trim();
  if(!q)return;
  appendMessage("user",q);
  input.value="";
  const bubble=appendLoading();
  let typingTimer=null;
  let displayQueue=[];
  let displayedText="";
  let streamDone=false;
  function typeNextChar(){
    if(displayQueue.length===0){
      if(streamDone){
        if(!displayedText){
          bubble.innerHTML="抱歉，没有获得有效回答。";
        }else if(typeof marked!=="undefined"){
          bubble.innerHTML=DOMPurify.sanitize(marked.parse(displayedText));
        }else{
          bubble.innerHTML=displayedText.replace(/\n/g,"<br>");
        }
        return;
      }
      typingTimer=setTimeout(typeNextChar,50);
      return;
    }
    displayedText+=displayQueue.shift();
    bubble.innerHTML=displayedText.replace(/\n/g,"<br>");
    const container=document.getElementById("aiMessages");
    container.scrollTop=container.scrollHeight;
    typingTimer=setTimeout(typeNextChar,30);
  }
  try{
    const relevant=selectRelevantCases(q,6000);
    const messages=[
      {role:"system",content:systemPrompt+"\n\n案例数据："+JSON.stringify(relevant)},
      {role:"user",content:q}
    ];
    const resp=await fetch("https://api.moonshot.cn/v1/chat/completions",{
      method:"POST",
      headers:{
        "Content-Type":"application/json",
        "Authorization":"Bearer sk-ttACQINTYwQrwKIpPIiIhDJfVkWPrYiLY14Vm1kn8SRAr5nS"
      },
      body:JSON.stringify({model:llmConfig.model,messages:messages,temperature:1,max_tokens:4096,stream:true}),
      cache:"no-store",
      keepalive:false
    });
    if(!resp.ok){
      clearTimeout(typingTimer);
      const data=await resp.json();
      let errMsg="HTTP "+resp.status;
      if(data.error&&data.error.message){errMsg+="："+data.error.message;}
      else if(data.message){errMsg+="："+data.message;}
      bubble.parentElement.remove();
      appendMessage("bot","请求失败："+errMsg);
      return;
    }
    bubble.innerHTML="";
    bubble.parentElement.classList.remove("ai-loading");
    const reader=resp.body.getReader();
    const decoder=new TextDecoder();
    let buffer="";
    typeNextChar();
    while(true){
      const {done,value}=await reader.read();
      if(done){streamDone=true;break;}
      buffer+=decoder.decode(value,{stream:true});
      const lines=buffer.split("\n");
      buffer=lines.pop();
      for(const line of lines){
        const t=line.trim();
        if(!t||!t.startsWith("data: "))continue;
        const ds=t.slice(6);
        if(ds==="[DONE]")continue;
        try{
          const chunk=JSON.parse(ds);
          if(chunk.choices&&chunk.choices[0]&&chunk.choices[0].delta&&chunk.choices[0].delta.content){
            const content=chunk.choices[0].delta.content;
            for(const ch of content)displayQueue.push(ch);
          }
        }catch(e){}
      }
    }
    streamDone=true;
  }catch(e){
    clearTimeout(typingTimer);
    bubble.parentElement.remove();
    appendMessage("bot","请求异常："+e.message);
  }
}
