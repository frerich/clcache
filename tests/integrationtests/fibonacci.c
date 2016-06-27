#include <stdio.h>

void print_fib(int n)
{
    int a = 0;
    int b = 1;
    while (a < n)
    {
        printf("%d ", a);

        int old_a = a;
        a = b;
        b = old_a + b;
    }
}

int main()
{
    print_fib(500);
    printf("\n");
    return 0;
}
