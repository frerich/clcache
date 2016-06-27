// APPLIB.CPP : This file contains the code that implements
//              the interface code declared in the header
//              files STABLE.H, ANOTHER.H, and UNSTABLE.H.
//
#include "another.h"
#include "stable.h"
#include "unstable.h"

// The following code represents code that is deemed stable and
// not likely to change. The associated interface code is
// precompiled. In this example, the header files STABLE.H and
// ANOTHER.H are precompiled.
void savetime( void )
{
    std::cout << "Why recompile stable code?" << std::endl;
}

void savemoretime( void )
{
    std::cout << "Why, indeed?" << std::endl;
}

// The following code represents code that is still under
// development. The associated header file is not precompiled.
void notstable( void )
{
    std::cout << "Unstable code requires"
              << " frequent recompilation." << std::endl;
}
