#include <cstdint>
#include <iostream>

namespace {

void print_fib(int n)
{
    std::int32_t a = 0;
    std::int32_t b = 1;
    while (a < n)
    {
        std::cout << a << " ";

        auto old_a = a;
        a = b;
        b = old_a + b;
    }

    std::cout << std::endl;
}

}

int main()
{
    print_fib(5);
    return 0;
}
