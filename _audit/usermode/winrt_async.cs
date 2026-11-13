using System;
using System.Threading.Tasks;
public static class WinRTAsync {
    public static T Await<T>(this Windows.Foundation.IAsyncOperation<T> op) {
        return op.AsTask().GetAwaiter().GetResult();
    }
}