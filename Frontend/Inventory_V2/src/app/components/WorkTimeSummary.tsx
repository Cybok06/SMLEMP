import { useEffect, useState } from "react";

type AttendanceSummary = { reported_days:number; late_days:number; approved_absence_days:number; approved_absence_occasions:number; missing_clock_in_days:number; pending_leave_requests:number; from:string; to:string };
export function WorkTimeSummary() {
  const [summary,setSummary]=useState<AttendanceSummary|null>(null);
  const [error,setError]=useState("");
  useEffect(()=>{
    const controller=new AbortController();
    const load=async()=>{try{const response=await fetch("/attendance/api/summary",{credentials:"same-origin",signal:controller.signal});const data=await response.json();if(!response.ok||!data.ok)throw Error(data.message||"Unable to load attendance.");if(!controller.signal.aborted){setSummary(data.summary);setError("");}}catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:"Unable to load attendance.");}};
    void load();window.addEventListener("focus",load);window.addEventListener("attendance-updated",load);
    return()=>{controller.abort();window.removeEventListener("focus",load);window.removeEventListener("attendance-updated",load);};
  },[]);
  return <section aria-label="My attendance and leave" style={{border:"1px solid #dce4ed",borderRadius:16,padding:20,marginBottom:24,background:"#fff",color:"#172c42"}}>
    <div style={{display:"flex",justifyContent:"space-between",gap:16,flexWrap:"wrap"}}><div><h2 style={{fontSize:18,fontWeight:700}}>My attendance &amp; leave</h2><p style={{fontSize:12,color:"#64748b"}}>{summary?`${summary.from} to ${summary.to}`:"This month"}</p></div><a href="/log-time" style={{color:"#174e65",fontWeight:600}}>Log Time / Request Leave</a></div>
    {error?<p role="alert">{error}</p>:!summary?<p>Loading attendance...</p>:<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(120px,1fr))",gap:12,marginTop:16}}>{[["Workdays reported",summary.reported_days],["Late arrivals",summary.late_days],["Absence occasions",summary.approved_absence_occasions],["Approved absence days",summary.approved_absence_days],["Missing clock-ins",summary.missing_clock_in_days],["Pending requests",summary.pending_leave_requests]].map(([label,value])=><div key={String(label)} style={{padding:12,borderRadius:10,background:"#f2f6fa",fontSize:12}}>{label}<strong style={{display:"block",fontSize:24,marginTop:6}}>{value}</strong></div>)}</div>}
    <p style={{fontSize:12,color:"#64748b",marginTop:12}}>Approved absence counts past scheduled leave days without a clock-in. Missing clock-ins are shown separately.</p>
  </section>;
}
