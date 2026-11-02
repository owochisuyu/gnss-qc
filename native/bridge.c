/* Small C ABI over the unmodified, vendored RTKLIB 2.4.3 b34. */
#include "rtklib.h"
#ifdef _WIN32
#define QC_API __declspec(dllexport)
#else
#define QC_API __attribute__((visibility("default")))
#endif

int showmsg(const char *format, ...) { return 0; }
void settspan(gtime_t ts, gtime_t te) {}
void settime(gtime_t time) {}

static gtime_t qc_time(double stamp) {
    gtime_t t; t.time=(time_t)floor(stamp); t.sec=stamp-floor(stamp); return t;
}
QC_API void *qc_open(const char *path, int *counts) {
    nav_t *nav=(nav_t *)calloc(1,sizeof(nav_t));
    if (!nav) return NULL;
    if (readrnx(path,1,"",NULL,nav,NULL)<=0) { freenav(nav,0xFF); free(nav); return NULL; }
    uniqnav(nav);
    counts[0]=nav->n; counts[1]=nav->ng; counts[2]=nav->ns;
    if (!(nav->n+nav->ng+nav->ns)) { freenav(nav,0xFF); free(nav); return NULL; }
    return nav;
}
QC_API void qc_close(void *p) { if(p){ freenav((nav_t *)p,0xFF); free(p); } }
QC_API int qc_sat(const char *id) { return satid2no(id); }
QC_API double qc_freq(void *p, const char *id, const char *code, int channel) {
    int sat=satid2no(id),sys=satsys(sat,NULL); uint8_t c=obs2code(code);
    if (sys==SYS_GLO && channel>=-7 && channel<=6) return code2freq(sys,c,channel);
    return sat2freq(sat,c,(nav_t *)p);
}
QC_API double qc_gpst(double stamp, int system) {
    gtime_t t=qc_time(stamp);
    if(system==1) t=utc2gpst(t);
    if(system==2) t=bdt2gpst(t);
    return (double)t.time+t.sec;
}
/* Rows: az [rad], el [rad], X,Y,Z [m], status (0 valid,1 no eph,2 unhealthy). */
QC_API void qc_geometry(void *p, double stamp, int n, const int *sats,
                        const double *rr, double *rows) {
    nav_t *nav=(nav_t *)p; gtime_t t=qc_time(stamp);
    double pos[3],rs[6],dts[2],var,e[3],azel[2]; int i,health;
    ecef2pos(rr,pos);
    for(i=0;i<n;i++) {
        double *r=rows+6*i; memset(r,0,sizeof(double)*6); r[5]=1;
        if(!satpos(t,t,sats[i],EPHOPT_BRDC,nav,rs,dts,&var,&health)) continue;
        if(geodist(rs,rr,e)<=0.0) continue;
        satazel(pos,e,azel);
        r[0]=azel[0]; r[1]=azel[1]; r[2]=rs[0]; r[3]=rs[1]; r[4]=rs[2];
        r[5]=health==0?0:2;
    }
}
QC_API void qc_dops(int n, const double *azel, double mask, double *dop) {
    dops(n,azel,mask,dop);
}
QC_API void qc_pos(const double *rr, double *pos) { ecef2pos(rr,pos); }
/* Header position fallback: a genuine RTKLIB single point solution. */
QC_API int qc_spp(void *p,double stamp,int n,const int *sats,const double *P,
                  const char *codes,double *rr,char *message) {
    obsd_t obs[MAXOBS]={{0}}; sol_t sol={0}; prcopt_t opt=prcopt_default;
    int i; opt.navsys=SYS_ALL; opt.elmin=10.0*D2R;
    if(n>MAXOBS) n=MAXOBS;
    for(i=0;i<n;i++) {
        obs[i].time=qc_time(stamp); obs[i].sat=sats[i]; obs[i].rcv=1;
        obs[i].P[0]=P[i]; obs[i].code[0]=obs2code(codes+3*i);
    }
    if(!pntpos(obs,n,(nav_t *)p,&opt,&sol,NULL,NULL,message)) return 0;
    memcpy(rr,sol.rr,sizeof(double)*3); return 1;
}
