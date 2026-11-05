import {afterEach, expect, it, vi} from 'vitest';
import {paintLoading} from '../src/loader';
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
it('continues loading in a hidden iframe without animation frames', async()=>{
 vi.useFakeTimers();
 vi.stubGlobal('requestAnimationFrame',vi.fn(()=>1));
 vi.stubGlobal('cancelAnimationFrame',vi.fn());
 const ready=vi.fn();
 const pending=paintLoading().then(ready);
 await vi.advanceTimersByTimeAsync(79);expect(ready).not.toHaveBeenCalled();
 await vi.advanceTimersByTimeAsync(1);await pending;expect(ready).toHaveBeenCalledOnce();
});
